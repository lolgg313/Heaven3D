# @heaven3d-part editor/gizmo.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 12 —  GIZMO RENDERER  (Unity-style move/rotate/scale)
# #############################################################################

class Gizmo:
    """
    Unity-style transform gizmo with move / rotate / scale modes.

    Implemented as actual 3D triangle geometry (cylinders for axis shafts,
    cones for arrowheads, torus rings for rotation) rather than GL_LINES —
    modern GPU drivers do not support line thickness > 1px, so a gizmo drawn
    with LINES is invisible/un-clickable.  Triangle geometry is volumetric,
    visible at any size, and ray-castable for interaction.

    Interaction: on mouse-down the engine calls `pick()` first; if a handle
    is hit the gizmo enters a drag state and `update_drag()` projects mouse
    movement onto the selected axis/plane each frame.
    """
    MOVE, ROTATE, SCALE = "move", "rotate", "scale"

    # World-space axis vectors (gizmo is axis-aligned, never rotated)
    AXES = {
        "x": glm.vec3(1, 0, 0),
        "y": glm.vec3(0, 1, 0),
        "z": glm.vec3(0, 0, 1),
    }

    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        self.mode = self.MOVE
        self.target: Optional[SceneNode] = None
        self.visible = False

        # Geometry + render state.  We build one merged vertex buffer per mode
        # (move / rotate / scale) and tag every vertex with the axis it belongs
        # to so the pick test can resolve which handle was hit.
        self._vbo: Dict[str, moderngl.Buffer] = {}
        self._vao: Dict[str, moderngl.VertexArray] = {}
        self._axis_ranges: Dict[str, List[Tuple[str, int, int]]] = {}
        # _axis_ranges[mode] = list of (axis_name, start_vertex, count)

        # Interaction state
        self.dragging = False
        self.drag_axis: Optional[str] = None
        self._drag_start_pos = glm.vec3(0)
        self._drag_start_value = 0.0   # start pos-on-axis / angle / scale
        self._drag_start_node_pos = glm.vec3(0)
        self._drag_start_node_rot = glm.vec3(0)
        self._drag_start_node_scale = glm.vec3(1)
        self._drag_plane_n = glm.vec3(0, 1, 0)
        self._drag_plane_p = glm.vec3(0)

        self._build_geometry()

    # =============================================================== geometry
    @staticmethod
    def _cylinder(axis: glm.vec3, p0: glm.vec3, length: float,
                  radius: float, color: Tuple[float, float, float],
                  segments: int = 12, axis_name: str = "x",
                  out: list = None):
        """Append a cylinder (TRIANGLES) along `axis` from p0 to p0+axis*length.
        Each vertex is (px, py, pz, cr, cg, cb).  Records the vertex span."""
        start = len(out) // 6
        # Build an orthonormal basis (axis, u, v)
        a = glm.normalize(axis)
        ref = glm.vec3(0, 1, 0) if abs(a.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(a, ref))
        v = glm.cross(a, u)
        # Ring vertices at the two caps
        ring0 = []
        ring1 = []
        for i in range(segments):
            ang = (i / segments) * math.pi * 2
            offset = (u * math.cos(ang) + v * math.sin(ang)) * radius
            ring0.append(p0 + offset)
            ring1.append(p0 + a * length + offset)
        # Side quads → 2 triangles each
        for i in range(segments):
            j = (i + 1) % segments
            for p in (ring0[i], ring0[j], ring1[i],
                      ring1[i], ring0[j], ring1[j]):
                out.extend([p.x, p.y, p.z, *color])
        return (axis_name, start, (len(out) // 6) - start)

    @staticmethod
    def _cone(axis: glm.vec3, base: glm.vec3, height: float,
              radius: float, color, segments: int = 12,
              axis_name: str = "x", out: list = None):
        """Append a cone (TRIANGLES) pointing along `axis` from `base`."""
        start = len(out) // 6
        a = glm.normalize(axis)
        ref = glm.vec3(0, 1, 0) if abs(a.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(a, ref))
        v = glm.cross(a, u)
        tip = base + a * height
        ring = []
        for i in range(segments):
            ang = (i / segments) * math.pi * 2
            offset = (u * math.cos(ang) + v * math.sin(ang)) * radius
            ring.append(base + offset)
        for i in range(segments):
            j = (i + 1) % segments
            # side triangle
            for p in (ring[i], ring[j], tip):
                out.extend([p.x, p.y, p.z, *color])
            # cap triangle
            for p in (ring[j], ring[i], base):
                out.extend([p.x, p.y, p.z, *color])
        return (axis_name, start, (len(out) // 6) - start)

    @staticmethod
    def _torus(axis: glm.vec3, center: glm.vec3, radius: float,
               tube: float, color, major_seg: int = 48, minor_seg: int = 8,
               axis_name: str = "x", out: list = None):
        """Append a torus (TRIANGLES) in the plane normal to `axis`."""
        start = len(out) // 6
        a = glm.normalize(axis)
        ref = glm.vec3(0, 1, 0) if abs(a.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(a, ref))
        v = glm.cross(a, u)
        # Build a grid of vertices (major around the ring, minor around the tube)
        grid = [[None] * minor_seg for _ in range(major_seg)]
        for i in range(major_seg):
            ma = (i / major_seg) * math.pi * 2
            ring_center = center + (u * math.cos(ma) + v * math.sin(ma)) * radius
            # local tangent basis around the tube
            tu = glm.normalize(u * math.cos(ma) + v * math.sin(ma))
            tv = a
            for j in range(minor_seg):
                mi = (j / minor_seg) * math.pi * 2
                p = ring_center + (tu * math.cos(mi) + tv * math.sin(mi)) * tube
                grid[i][j] = p
        for i in range(major_seg):
            i2 = (i + 1) % major_seg
            for j in range(minor_seg):
                j2 = (j + 1) % minor_seg
                p00, p01, p10, p11 = grid[i][j], grid[i][j2], grid[i2][j], grid[i2][j2]
                for p in (p00, p10, p01,  p01, p10, p11):
                    out.extend([p.x, p.y, p.z, *color])
        return (axis_name, start, (len(out) // 6) - start)

    @staticmethod
    def _box(center: glm.vec3, size: float, color,
             axis_name: str = "c", out: list = None):
        """Append a small cube (TRIANGLES) — used for scale handles."""
        start = len(out) // 6
        s = size * 0.5
        # 8 corners
        c = [
            center + glm.vec3(-s, -s, -s), center + glm.vec3( s, -s, -s),
            center + glm.vec3( s,  s, -s), center + glm.vec3(-s,  s, -s),
            center + glm.vec3(-s, -s,  s), center + glm.vec3( s, -s,  s),
            center + glm.vec3( s,  s,  s), center + glm.vec3(-s,  s,  s),
        ]
        faces = [(0,1,2,3),(5,4,7,6),(4,0,3,7),(1,5,6,2),(3,2,6,7),(4,5,1,0)]
        for a,b,cc,d in faces:
            for p in (c[a], c[b], c[cc],  c[cc], c[b], c[d]):
                out.extend([p.x, p.y, p.z, *color])
        return (axis_name, start, (len(out) // 6) - start)

    def _build_geometry(self):
        """Build merged triangle geometry for all three gizmo modes."""
        shaft_len = 1.0
        shaft_r = 0.025
        head_len = 0.22
        head_r = 0.09
        ring_radius = 1.0
        ring_tube = 0.022

        # ---- MOVE: 3 cylinders + 3 cones ----
        verts = []
        ranges = []
        for ax in ("x", "y", "z"):
            a = self.AXES[ax]
            col = GIZMO_COLORS[ax]
            ranges.append(self._cylinder(a, glm.vec3(0), shaft_len, shaft_r,
                                         col, 10, ax, verts))
            ranges.append(self._cone(a, a * shaft_len, head_len, head_r,
                                     col, 12, ax, verts))
        # Center cube (free-move handle)
        ranges.append(self._box(glm.vec3(0), 0.14, GIZMO_COLORS["c"], "c", verts))
        self._move_verts = np.array(verts, dtype=np.float32)
        self._axis_ranges[self.MOVE] = ranges

        # ---- ROTATE: 3 toruses ----
        verts = []
        ranges = []
        for ax in ("x", "y", "z"):
            ranges.append(self._torus(self.AXES[ax], glm.vec3(0),
                                      ring_radius, ring_tube,
                                      GIZMO_COLORS[ax], 48, 6, ax, verts))
        self._rotate_verts = np.array(verts, dtype=np.float32)
        self._axis_ranges[self.ROTATE] = ranges

        # ---- SCALE: 3 cylinders + 3 box ends ----
        verts = []
        ranges = []
        for ax in ("x", "y", "z"):
            a = self.AXES[ax]
            col = GIZMO_COLORS[ax]
            ranges.append(self._cylinder(a, glm.vec3(0), shaft_len, shaft_r,
                                         col, 10, ax, verts))
            ranges.append(self._box(a * (shaft_len + 0.06), 0.13, col, ax, verts))
        self._scale_verts = np.array(verts, dtype=np.float32)
        self._axis_ranges[self.SCALE] = ranges

    def _get_vao(self, prog: moderngl.Program) -> moderngl.VertexArray:
        """Return (and cache) the VAO for the current mode."""
        if self.mode in self._vao:
            return self._vao[self.mode]
        data = {self.MOVE: self._move_verts,
                self.ROTATE: self._rotate_verts,
                self.SCALE: self._scale_verts}[self.mode]
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(prog, [(vbo, "3f 3f", "in_position", "in_color")])
        vao._vbo = vbo  # keep alive
        self._vbo[self.mode] = vbo
        self._vao[self.mode] = vao
        return vao

    # =============================================================== picking
    def pick(self, ray_origin: glm.vec3, ray_dir: glm.vec3) -> Optional[str]:
        """Ray-cast against the current gizmo mode's handles.
        Returns the axis name ("x"/"y"/"z"/"c") of the closest hit, or None.
        Uses an analytical cylinder/torus test against each handle rather
        than per-triangle intersection (fast and accurate enough for UI)."""
        if not self.visible or self.target is None:
            return None
        # Gizmo sits at the target's world position.
        gizmo_origin = self.target.world_position()

        best_t = float("inf")
        best_axis = None

        if self.mode == self.MOVE:
            # Shafts + cones: approximate each axis as a single fat cylinder
            for ax in ("x", "y", "z"):
                a = self.AXES[ax]
                t = self._raycast_cylinder(ray_origin, ray_dir,
                                           gizmo_origin, a, 1.22, 0.10)
                if t is not None and t < best_t:
                    best_t, best_axis = t, ax
            # Center free-move cube
            t = self._raycast_sphere(ray_origin, ray_dir, gizmo_origin, 0.16)
            if t is not None and t < best_t:
                best_t, best_axis = t, "c"

        elif self.mode == self.ROTATE:
            # Each ring is a torus; approximate as a thin torus test
            for ax in ("x", "y", "z"):
                a = self.AXES[ax]
                t = self._raycast_torus(ray_origin, ray_dir,
                                        gizmo_origin, a, 1.0, 0.06)
                if t is not None and t < best_t:
                    best_t, best_axis = t, ax

        elif self.mode == self.SCALE:
            for ax in ("x", "y", "z"):
                a = self.AXES[ax]
                # shaft
                t = self._raycast_cylinder(ray_origin, ray_dir,
                                           gizmo_origin, a, 1.06, 0.06)
                if t is not None and t < best_t:
                    best_t, best_axis = t, ax
                # end box (sphere approx)
                t = self._raycast_sphere(ray_origin, ray_dir,
                                         gizmo_origin + a * 1.06, 0.13)
                if t is not None and t < best_t:
                    best_t, best_axis = t, ax
        return best_axis

    @staticmethod
    def _raycast_sphere(ro, rd, center, radius) -> Optional[float]:
        oc = ro - center
        b = glm.dot(oc, rd)
        c = glm.dot(oc, oc) - radius * radius
        disc = b * b - c
        if disc < 0:
            return None
        t = -b - math.sqrt(disc)
        return t if t >= 0 else None

    @staticmethod
    def _raycast_cylinder(ro, rd, base, axis, length, radius) -> Optional[float]:
        """Infinite-cylinder test bounded by [0, length] along `axis`."""
        a = glm.normalize(axis)
        # Project ray and base into the plane basis perpendicular to axis
        d_perp = rd - a * glm.dot(rd, a)
        oc = ro - base
        oc_perp = oc - a * glm.dot(oc, a)
        A = glm.dot(d_perp, d_perp)
        if A < 1e-8:
            return None
        B = 2.0 * glm.dot(d_perp, oc_perp)
        C = glm.dot(oc_perp, oc_perp) - radius * radius
        disc = B * B - 4.0 * A * C
        if disc < 0:
            return None
        sq = math.sqrt(disc)
        for t in sorted([(-B - sq) / (2 * A), (-B + sq) / (2 * A)]):
            if t < 0:
                continue
            h = glm.dot((ro + rd * t) - base, a)
            if 0 <= h <= length:
                return t
        return None

    @staticmethod
    def _raycast_torus(ro, rd, center, axis, major, minor) -> Optional[float]:
        """Cheap torus pick: raycast against the ring as a swept circle.
        Steps the ray and checks distance to the ring centre-line."""
        a = glm.normalize(axis)
        # Sample the ray in small steps; return the first t where the point is
        # within `minor` of the ring (centre-line = circle of radius `major`).
        # Cheap and robust; good enough for UI gizmo picking.
        best_t = None
        t = 0.0
        step = 0.05
        max_t = 100.0
        while t < max_t:
            p = ro + rd * t
            rel = p - center
            # distance to the ring plane
            plane_d = glm.dot(rel, a)
            # project onto the ring plane
            in_plane = rel - a * plane_d
            r = glm.length(in_plane)
            if r > 1e-6:
                # closest point on the ring centre-line
                ring_pt = (in_plane / r) * major
                dist_to_ring = glm.length(p - (center + ring_pt))
                if dist_to_ring <= minor:
                    return t
            t += step
        return None

    # =============================================================== dragging
    def begin_drag(self, axis: str, ray_origin: glm.vec3, ray_dir: glm.vec3,
                   camera: Camera):
        """Begin a drag on `axis`.  Captures the node's starting transform
        and the drag plane used to map mouse motion → world delta."""
        self.dragging = True
        self.drag_axis = axis
        self._drag_start_node_pos = glm.vec3(self.target.position)
        self._drag_start_node_rot = glm.vec3(self.target.rotation_euler)
        self._drag_start_node_scale = glm.vec3(self.target.scale)
        gizmo_pos = self.target.world_position()

        if self.mode == self.MOVE:
            if axis == "c":
                # Free move: drag on a plane facing the camera
                cam_fwd = camera.forward()
                self._drag_plane_n = -cam_fwd
                self._drag_plane_p = gizmo_pos
            else:
                # Axis move: drag on the plane that CONTAINS the axis while
                # facing the camera as directly as possible.
                # Double-cross construction: project the camera's forward onto
                # the plane perpendicular to the axis, then use that projected
                # vector as the plane normal.  This avoids the previous edge-on
                # bug where cross(axis, cam_fwd) produced a plane parallel to
                # the view, making ray-plane hits numerically unstable.
                a = self.AXES[axis]
                cam_fwd = camera.forward()
                self._drag_plane_n = glm.normalize(
                    glm.cross(glm.cross(cam_fwd, a), a))
                self._drag_plane_p = gizmo_pos
            hit = self._ray_plane(ray_origin, ray_dir,
                                  self._drag_plane_p, self._drag_plane_n)
            self._drag_start_pos = hit if hit else gizmo_pos

        elif self.mode == self.ROTATE:
            # Rotate on the plane normal to the selected axis
            self._drag_plane_n = self.AXES[axis]
            self._drag_plane_p = gizmo_pos
            # Build a stable in-plane 2D basis (u, v) on the rotation plane.
            # Using glm.cross(plane_n, camera.up()) blows up when the two are
            # parallel (e.g. Y-axis drag with default up=(0,1,0) → zero
            # vector → atan2 returns 0 → no rotation).  Pick the world axis
            # least aligned with plane_n as the reference instead.
            n = self._drag_plane_n
            refs = [glm.vec3(1, 0, 0), glm.vec3(0, 1, 0), glm.vec3(0, 0, 1)]
            ref = min(refs, key=lambda r: abs(glm.dot(r, n)))
            self._drag_basis_u = glm.normalize(glm.cross(n, ref))
            self._drag_basis_v = glm.cross(n, self._drag_basis_u)
            hit = self._ray_plane(ray_origin, ray_dir,
                                  self._drag_plane_p, self._drag_plane_n)
            if hit:
                d = hit - gizmo_pos
                self._drag_start_value = math.degrees(math.atan2(
                    glm.dot(d, self._drag_basis_v),
                    glm.dot(d, self._drag_basis_u)))
            else:
                self._drag_start_value = 0.0

        elif self.mode == self.SCALE:
            a = self.AXES[axis]
            cam_fwd = camera.forward()
            # Same double-cross construction as MOVE: keep the axis in the
            # drag plane while facing the camera, eliminating the edge-on
            # raycast instability the previous cross(a, cam_fwd) produced.
            self._drag_plane_n = glm.normalize(
                glm.cross(glm.cross(cam_fwd, a), a))
            self._drag_plane_p = gizmo_pos
            hit = self._ray_plane(ray_origin, ray_dir,
                                  self._drag_plane_p, self._drag_plane_n)
            self._drag_start_pos = hit if hit else gizmo_pos

    def update_drag(self, ray_origin: glm.vec3, ray_dir: glm.vec3,
                    camera: Camera):
        """Project the current mouse ray onto the drag plane and apply the
        resulting delta/angle/scale to the target node."""
        if not self.dragging or self.target is None:
            return
        hit = self._ray_plane(ray_origin, ray_dir,
                              self._drag_plane_p, self._drag_plane_n)
        if hit is None:
            return
        gizmo_pos = self.target.world_position()

        if self.mode == self.MOVE:
            if self.drag_axis == "c":
                delta = hit - self._drag_start_pos
                self.target.position = self._drag_start_node_pos + delta
            else:
                a = self.AXES[self.drag_axis]
                # Project the mouse delta onto the axis vector
                delta = hit - self._drag_start_pos
                amount = glm.dot(delta, a)
                self.target.position = self._drag_start_node_pos + a * amount

        elif self.mode == self.ROTATE:
            d = hit - gizmo_pos
            # Reuse the stable in-plane basis captured in begin_drag — avoids
            # the zero-cross-product bug on the Y axis.
            current_angle = math.degrees(math.atan2(
                glm.dot(d, self._drag_basis_v),
                glm.dot(d, self._drag_basis_u)))
            delta_angle = current_angle - self._drag_start_value
            axis_idx = {"x": 0, "y": 1, "z": 2}[self.drag_axis]
            new_rot = glm.vec3(self._drag_start_node_rot)
            new_rot[axis_idx] = self._drag_start_node_rot[axis_idx] + delta_angle
            self.target.rotation_euler = new_rot

        elif self.mode == self.SCALE:
            a = self.AXES[self.drag_axis]
            delta = hit - self._drag_start_pos
            amount = glm.dot(delta, a)
            # Convert world delta into a scale factor
            factor = max(0.05, 1.0 + amount * 0.5)
            axis_idx = {"x": 0, "y": 1, "z": 2}[self.drag_axis]
            new_scale = glm.vec3(self._drag_start_node_scale)
            new_scale[axis_idx] = max(0.05,
                self._drag_start_node_scale[axis_idx] * factor)
            self.target.scale = new_scale

    def end_drag(self):
        self.dragging = False
        self.drag_axis = None

    @staticmethod
    def _ray_plane(ro, rd, plane_p, plane_n) -> Optional[glm.vec3]:
        denom = glm.dot(rd, plane_n)
        if abs(denom) < 1e-6:
            return None
        t = glm.dot(plane_p - ro, plane_n) / denom
        if t < 0:
            return None
        return ro + rd * t

    # =============================================================== render
    def set_mode(self, mode: str):
        if mode in (self.MOVE, self.ROTATE, self.SCALE):
            self.mode = mode

    def set_target(self, node: Optional[SceneNode]):
        self.target = node
        self.visible = node is not None

    def render(self, camera: Camera):
        if not self.visible or self.target is None:
            return
        prog = ShaderLib.get(self.ctx, "line")
        if prog is None:
            return
        vao = self._get_vao(prog)
        # Gizmo stays axis-aligned: model = translation to target world pos
        model = glm.translate(glm.mat4(1.0), self.target.world_position())
        prog["u_view"].write(camera.view().to_bytes())
        prog["u_proj"].write(camera.projection().to_bytes())
        prog["u_model"].write(model.to_bytes())
        # Draw on top of everything (no depth test), with blending off so the
        # solid gizmo colours read cleanly.
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.BLEND)
        vao.render(moderngl.TRIANGLES)
        self.ctx.enable(moderngl.DEPTH_TEST)

    def release(self):
        for vao in self._vao.values():
            try: vao.release()
            except Exception: pass
        self._vao.clear()
        self._vbo.clear()


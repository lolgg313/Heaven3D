# @heaven3d-part editor/collider_editor.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 12c —  COLLIDER SHAPE EDITOR  (Unity-style green outline + handles)
# #############################################################################

class ColliderShapeEditor:
    """Unity-style collider edit mode for the editor's "Edit Shape" button.

    While a node with a collider is selected the collider's outer debug
    outline is drawn in Unity green on top of the scene (exactly like
    Unity's scene-view collider gizmo).  Pressing "Edit Shape" in the
    Inspector enters edit mode: small square GRAB HANDLES appear on the
    shape — drag them with the left mouse button to resize the collider.
    The numbers are written straight back into the Collider (size / center,
    radius, height, plane size) so everything still round-trips through
    save/load and the physics world.

    Handles per shape (4 grab points, Unity style — the box gets 6 because
    a 3D box has three axes):
      * Box     → 12 green edges + a square handle at every face centre.
                  Dragging a face moves THAT face (size + centre), exactly
                  like Unity's BoxCollider edit mode.
      * Sphere  → 3 axis circles + 4 handles on the camera-facing
                  silhouette circle; all four edit the radius.
      * Capsule → cap circles + 4 silhouette rails; top/bottom handles
                  edit the height (centre stays fixed), the 2 side handles
                  edit the radius.
      * Plane   → square outline + 4 corner handles resizing the bounded
                  square (dragging a handle on an INFINITE plane turns it
                  into a bounded one).
      * Mesh    → green wireframe of the actual collision triangles (the
                  world-space BVH triangles, so you see EXACTLY what the
                  physics uses).  No handles — reshape the mesh itself or
                  move/scale the node.

    The outline is 1px GL_LINES (an outline needs no thickness); the
    handles are small triangle cubes like the transform gizmo so they are
    visible and ray-pickable at any distance.  Everything is drawn with
    the depth test off, on top of the scene — the Unity gizmo look.
    """

    OUTLINE_COLOR = (0.29, 0.86, 0.36)   # Unity collider green
    HANDLE_COLOR  = (0.13, 0.55, 0.20)   # darker green fill
    HANDLE_HOVER  = (0.98, 0.85, 0.25)   # amber hover highlight
    HANDLE_DRAG   = (1.00, 1.00, 1.00)   # white while dragging

    _SEGMENTS = 48           # segments per outline circle
    _MAX_LINE_VERTS = 49152  # reserved dynamic-VBO capacity (mesh
                             # wireframes: ~8k triangles of green lines)
    _MAX_TRI_VERTS = 2048

    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        self.active = False
        self.node: Optional[SceneNode] = None
        # Called after every drag update so the engine can re-register the
        # node with the live physics world (wired to _physics_reregister).
        self.on_change: Optional[Callable[[SceneNode], None]] = None

        self._prog = ShaderLib.get(ctx, "line")
        self._line_vbo = ctx.buffer(reserve=self._MAX_LINE_VERTS * 6 * 4,
                                    dynamic=True)
        self._tri_vbo = ctx.buffer(reserve=self._MAX_TRI_VERTS * 6 * 4,
                                   dynamic=True)
        if self._prog is not None:
            self._line_vao = ctx.vertex_array(
                self._prog, [(self._line_vbo, "3f 3f",
                              "in_position", "in_color")])
            self._tri_vao = ctx.vertex_array(
                self._prog, [(self._tri_vbo, "3f 3f",
                              "in_position", "in_color")])
        else:
            self._line_vao = None
            self._tri_vao = None

        # Per-frame geometry cache (rebuilt every render / pick / drag).
        self._line_verts: List[float] = []
        self._handles: List[dict] = []
        # Handle spec keys: id, kind, pos (world vec3), axis (world vec3 or
        # None), axis_idx, sign — plus per-shape extras.

        self._hover_id: Optional[int] = None
        self.dragging = False
        self._drag_handle: Optional[dict] = None
        self._drag_plane_n = glm.vec3(0, 1, 0)
        self._drag_plane_p = glm.vec3(0)
        self._drag_start_hit = glm.vec3(0)
        # Start-of-drag collider snapshot (local, unscaled values).
        self._start_size = glm.vec3(1)
        self._start_center = glm.vec3(0)
        self._start_radius = 0.5
        self._start_height = 1.8
        self._start_plane_size = 0.0
        self._start_scale = glm.vec3(1)

    # ------------------------------------------------------------ mode
    def enter(self, node: SceneNode):
        """Enter edit mode on `node` (must carry a collider)."""
        self.active = True
        self.node = node
        self.dragging = False
        self._drag_handle = None
        self._hover_id = None

    def exit(self):
        self.active = False
        self.node = None
        self.dragging = False
        self._drag_handle = None
        self._hover_id = None

    # ------------------------------------------------------------ geometry
    @staticmethod
    def _emit_line(out: list, a: glm.vec3, b: glm.vec3, color, cap: int):
        if (len(out) // 6) + 2 > cap:
            return
        out.extend([a.x, a.y, a.z, *color, b.x, b.y, b.z, *color])

    def _emit_circle(self, out: list, center: glm.vec3, u: glm.vec3,
                     v: glm.vec3, radius: float, color, cap: int):
        if radius <= 1e-5:
            return
        n = self._SEGMENTS
        prev = None
        for i in range(n + 1):
            a = (i % n) / n * math.tau
            p = center + (u * math.cos(a) + v * math.sin(a)) * radius
            if prev is not None:
                self._emit_line(out, prev, p, color, cap)
            prev = p

    @staticmethod
    def _view_basis(camera: Camera) -> Tuple[glm.vec3, glm.vec3]:
        """(right, up) unit vectors spanning the camera's view plane,
        robust when looking straight up/down."""
        fwd = camera.forward()
        ref = glm.vec3(0, 1, 0) if abs(fwd.y) < 0.95 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(fwd, ref))
        v = glm.normalize(glm.cross(u, fwd))
        return u, v

    @staticmethod
    def _perp_basis(axis: glm.vec3) -> Tuple[glm.vec3, glm.vec3]:
        ref = glm.vec3(0, 1, 0) if abs(axis.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(axis, ref))
        v = glm.cross(axis, u)
        return u, v

    def _handle_size(self, camera: Camera, pos: glm.vec3) -> float:
        """Screen-constant handle size in world units (≈14px on screen)."""
        dist = max(0.05, glm.length(pos - camera.position))
        world_per_px = (2.0 * dist *
                        math.tan(math.radians(camera.fov) * 0.5)) / 720.0
        return world_per_px * 14.0

    # ------------------------------------------------------ shape builders
    def _build(self, camera: Camera, node: SceneNode):
        """Rebuild the outline line-list and the handle spec list for
        `node`'s collider from its CURRENT world transform."""
        self._line_verts = []
        self._handles = []
        col = node.collider
        if col is None or isinstance(col, HeightfieldCollider):
            return
        shape = _shape_from_node_collider(node, col)
        kind = shape["kind"]
        C = self.OUTLINE_COLOR
        cap = self._MAX_LINE_VERTS
        out = self._line_verts

        if kind == "box":
            c, e, h = shape["c"], shape["e"], shape["h"]
            # 8 corners: corner[i + 2j + 4k]
            corners = []
            for k in (0, 1):
                for j in (0, 1):
                    for i in (0, 1):
                        corners.append(c + e[0] * ((i * 2 - 1) * h.x)
                                       + e[1] * ((j * 2 - 1) * h.y)
                                       + e[2] * ((k * 2 - 1) * h.z))
            edges = ((0, 1), (2, 3), (4, 5), (6, 7),      # vary i (x)
                     (0, 2), (1, 3), (4, 6), (5, 7),      # vary j (y)
                     (0, 4), (1, 5), (2, 6), (3, 7))      # vary k (z)
            for a, b in edges:
                self._emit_line(out, corners[a], corners[b], C, cap)
            # 6 face-centre grab handles (Unity's BoxCollider edit mode)
            for axis_idx in range(3):
                for sign in (1, -1):
                    pos = c + e[axis_idx] * (h[axis_idx] * sign)
                    self._handles.append({
                        "id": len(self._handles), "kind": "face",
                        "pos": pos, "axis": e[axis_idx],
                        "axis_idx": axis_idx, "sign": float(sign)})

        elif kind == "sphere":
            c, r = shape["c"], shape["r"]
            self._emit_circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 1, 0),
                              r, C, cap)
            self._emit_circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 0, 1),
                              r, C, cap)
            self._emit_circle(out, c, glm.vec3(0, 1, 0), glm.vec3(0, 0, 1),
                              r, C, cap)
            # 4 handles on the camera-facing silhouette circle
            u, v = self._view_basis(camera)
            for dirv in (u, -u, v, -v):
                self._handles.append({
                    "id": len(self._handles), "kind": "radius",
                    "pos": c + dirv * r, "axis": None,
                    "axis_idx": -1, "sign": 1.0, "center": c})

        elif kind == "capsule":
            p0, p1, r = shape["p0"], shape["p1"], shape["r"]
            span = p1 - p0
            axis = (_safe_normalize(span) if glm.length(span) > 1e-6
                    else _world_quat(node) * glm.vec3(0, 1, 0))
            u, v = self._perp_basis(axis)
            # cap circles perpendicular to the axis
            self._emit_circle(out, p0, u, v, r, C, cap)
            self._emit_circle(out, p1, u, v, r, C, cap)
            # cap profile circles containing the axis (reads as the domes)
            self._emit_circle(out, p0, axis, u, r, C, cap)
            self._emit_circle(out, p1, axis, u, r, C, cap)
            # 4 silhouette rails connecting the caps
            for dirv in (u, -u, v, -v):
                self._emit_line(out, p0 + dirv * r, p1 + dirv * r, C, cap)
            # top / bottom handles → height (centre fixed, grows symmetric)
            self._handles.append({
                "id": len(self._handles), "kind": "height",
                "pos": p1 + axis * r, "axis": axis,
                "axis_idx": 1, "sign": 1.0})
            self._handles.append({
                "id": len(self._handles), "kind": "height",
                "pos": p0 - axis * r, "axis": axis,
                "axis_idx": 1, "sign": -1.0})
            # 2 side handles on the camera-facing radial → radius
            mid = (p0 + p1) * 0.5
            cu, _ = self._view_basis(camera)
            radial = cu - axis * glm.dot(cu, axis)
            radial = (_safe_normalize(radial) if glm.length(radial) > 1e-5
                      else u)
            for dirv in (radial, -radial):
                self._handles.append({
                    "id": len(self._handles), "kind": "cradius",
                    "pos": mid + dirv * r, "axis": None,
                    "axis_idx": -1, "sign": 1.0,
                    "cap_axis": axis, "center": mid})

        elif kind == "plane":
            p, n = shape["p"], shape["n"]
            u, v = shape["u"], shape["v"]
            # An infinite plane gets a visual-only square so the user can
            # still see and grab it; dragging a corner makes it bounded.
            if shape["bounded"]:
                hx, hz = shape["hx"], shape["hz"]
            else:
                hx = hz = 4.0
            corners = [p - u * hx - v * hz, p + u * hx - v * hz,
                       p + u * hx + v * hz, p - u * hx + v * hz]
            for i in range(4):
                self._emit_line(out, corners[i], corners[(i + 1) % 4],
                                C, cap)
            for cp in corners:
                self._handles.append({
                    "id": len(self._handles), "kind": "corner",
                    "pos": cp, "axis": None, "axis_idx": -1, "sign": 1.0,
                    "plane_n": n, "plane_u": u, "plane_v": v,
                    "center": p})

        elif kind == "mesh":
            # Green wireframe of the EXACT collision triangles (world
            # space, straight from the cached BVH).  No grab handles —
            # a triangle soup has no size knob; reshape the mesh itself.
            bvh = shape.get("bvh")
            if bvh is not None and bvh.node_count > 0:
                tris = bvh.tris
                max_tris = (cap // 6) // 3
                for t in tris[:max_tris]:
                    a = glm.vec3(float(t[0][0]), float(t[0][1]),
                                 float(t[0][2]))
                    b = glm.vec3(float(t[1][0]), float(t[1][1]),
                                 float(t[1][2]))
                    c = glm.vec3(float(t[2][0]), float(t[2][1]),
                                 float(t[2][2]))
                    self._emit_line(out, a, b, C, cap)
                    self._emit_line(out, b, c, C, cap)
                    self._emit_line(out, c, a, C, cap)

    # ------------------------------------------------------------ picking
    def pick(self, ray_origin: glm.vec3, ray_dir: glm.vec3,
             camera: Camera) -> Optional[int]:
        """Return the id of the handle under the ray, or None."""
        if not self.active or self.node is None:
            return None
        self._build(camera, self.node)
        best_t = float("inf")
        best_id = None
        for h in self._handles:
            # generous pick radius: the whole on-screen handle + margin
            pr = self._handle_size(camera, h["pos"]) * 0.9
            t = Gizmo._raycast_sphere(ray_origin, ray_dir, h["pos"], pr)
            if t is not None and t < best_t:
                best_t, best_id = t, h["id"]
        return best_id

    def update_hover(self, ray_origin: glm.vec3, ray_dir: glm.vec3,
                     camera: Camera):
        self._hover_id = (None if self.dragging else
                          self.pick(ray_origin, ray_dir, camera))

    # ------------------------------------------------------------ dragging
    def begin_drag(self, handle_id: int, ray_origin: glm.vec3,
                   ray_dir: glm.vec3, camera: Camera):
        if self.node is None or self.node.collider is None:
            return
        h = next((x for x in self._handles if x["id"] == handle_id), None)
        if h is None:
            return
        col = self.node.collider
        self.dragging = True
        self._drag_handle = h
        # snapshot local (unscaled) collider params + world scale
        self._start_center = glm.vec3(col.center)
        self._start_scale = _world_scale(self.node)
        if isinstance(col, BoxCollider):
            self._start_size = glm.vec3(col.size)
        elif isinstance(col, SphereCollider):
            self._start_radius = float(col.radius)
        elif isinstance(col, CapsuleCollider):
            self._start_radius = float(col.radius)
            self._start_height = float(col.height)
        elif isinstance(col, PlaneCollider):
            self._start_plane_size = float(col.size)

        cam_fwd = camera.forward()
        if h["axis"] is not None:
            # 1-D drag along a world axis: plane contains the axis and
            # faces the camera (double-cross construction, same as Gizmo).
            a = h["axis"]
            self._drag_plane_n = glm.normalize(
                glm.cross(glm.cross(cam_fwd, a), a))
            self._drag_plane_p = h["pos"]
        elif h["kind"] == "corner":
            # Corner drag happens IN the plane collider's own plane.
            self._drag_plane_n = h["plane_n"]
            self._drag_plane_p = h["center"]
        elif h["kind"] == "cradius":
            # Radius drag: plane contains the capsule axis + radial dir.
            axis = h["cap_axis"]
            radial = _safe_normalize(h["pos"] - h["center"],
                                     glm.vec3(1, 0, 0))
            self._drag_plane_n = _safe_normalize(
                glm.cross(axis, radial), -cam_fwd)
            self._drag_plane_p = h["center"]
        else:
            # Free 2-D drag on the camera-facing plane (sphere radius).
            self._drag_plane_n = -cam_fwd
            self._drag_plane_p = h.get("center", h["pos"])
        hit = Gizmo._ray_plane(ray_origin, ray_dir,
                               self._drag_plane_p, self._drag_plane_n)
        self._drag_start_hit = hit if hit is not None else h["pos"]

    def update_drag(self, ray_origin: glm.vec3, ray_dir: glm.vec3,
                    camera: Camera):
        if not self.dragging or self._drag_handle is None:
            return
        node = self.node
        if node is None or node.collider is None:
            self.end_drag()
            return
        hit = Gizmo._ray_plane(ray_origin, ray_dir,
                               self._drag_plane_p, self._drag_plane_n)
        if hit is None:
            return
        h = self._drag_handle
        col = node.collider
        ws = self._start_scale

        if h["kind"] == "face" and isinstance(col, BoxCollider):
            i = h["axis_idx"]
            delta_w = glm.dot(hit - self._drag_start_hit, h["axis"])
            d_local = delta_w / max(abs(ws[i]), 1e-6)
            s = h["sign"]
            new_size = max(1e-4, self._start_size[i] + s * d_local)
            eff = s * (new_size - self._start_size[i])   # clamp-aware delta
            size = glm.vec3(col.size)
            size[i] = new_size
            col.size = size
            center = glm.vec3(col.center)
            center[i] = self._start_center[i] + eff * 0.5
            col.center = center

        elif h["kind"] == "radius" and isinstance(col, SphereCollider):
            r_world = glm.length(hit - h["center"])
            scale_max = max(ws.x, max(ws.y, ws.z), 1e-6)
            col.radius = max(1e-4, r_world / scale_max)

        elif h["kind"] == "height" and isinstance(col, CapsuleCollider):
            delta_w = glm.dot(hit - self._drag_start_hit, h["axis"])
            d_local = delta_w / max(abs(ws.y), 1e-6)
            # centre stays fixed: the capsule grows symmetric, so the
            # dragged tip moves at 2× the height delta.
            col.height = max(col.radius * 2.0,
                             self._start_height + 2.0 * h["sign"] * d_local)

        elif h["kind"] == "cradius" and isinstance(col, CapsuleCollider):
            axis = h["cap_axis"]
            d = hit - h["center"]
            radial = d - axis * glm.dot(d, axis)
            r_world = glm.length(radial)
            scale_max = max(ws.x, ws.z, 1e-6)
            col.radius = max(1e-4, r_world / scale_max)
            # invariant: height >= 2 × radius
            col.height = max(col.height, col.radius * 2.0)

        elif h["kind"] == "corner" and isinstance(col, PlaneCollider):
            d = hit - h["center"]
            hx_w = abs(glm.dot(d, h["plane_u"]))
            hz_w = abs(glm.dot(d, h["plane_v"]))
            size = max(2.0 * hx_w / max(ws.x, 1e-6),
                       2.0 * hz_w / max(ws.z, 1e-6))
            col.size = max(0.2, size)   # dragging an infinite plane bounds it

        if self.on_change is not None:
            try:
                self.on_change(node)
            except Exception:
                pass

    def end_drag(self):
        self.dragging = False
        self._drag_handle = None

    # ------------------------------------------------------------ render
    def render(self, camera: Camera, selected: Optional[SceneNode]):
        """Draw the collider outline for the selected node, plus grab
        handles while in edit mode.  Called once per editor frame."""
        if self._line_vao is None:
            return
        # Edit mode follows its own node; otherwise outline the selection.
        node = self.node if self.active else selected
        if node is None or node.collider is None:
            return
        self._build(camera, node)
        if self._line_verts:
            count = min(len(self._line_verts) // 6, self._MAX_LINE_VERTS)
            self._line_vbo.write(
                np.asarray(self._line_verts[:count * 6],
                           dtype=np.float32).tobytes())
            self._prog["u_view"].write(camera.view().to_bytes())
            self._prog["u_proj"].write(camera.projection().to_bytes())
            self._prog["u_model"].write(glm.mat4(1.0).to_bytes())
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.ctx.disable(moderngl.BLEND)
            self._line_vao.render(moderngl.LINES, vertices=count)
            self.ctx.enable(moderngl.DEPTH_TEST)

        # Grab handles (edit mode only) — small triangle cubes.
        if not self.active or node is not self.node or not self._handles:
            return
        tris: List[float] = []
        for h in self._handles:
            if self.dragging and self._drag_handle is h:
                color = self.HANDLE_DRAG
            elif h["id"] == self._hover_id:
                color = self.HANDLE_HOVER
            else:
                color = self.HANDLE_COLOR
            size = self._handle_size(camera, h["pos"])
            Gizmo._box(h["pos"], size, color, "c", tris)
        count = min(len(tris) // 6, self._MAX_TRI_VERTS)
        if count == 0:
            return
        self._tri_vbo.write(
            np.asarray(tris[:count * 6], dtype=np.float32).tobytes())
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.BLEND)
        self._tri_vao.render(moderngl.TRIANGLES, vertices=count)
        self.ctx.enable(moderngl.DEPTH_TEST)

    def release(self):
        for vao in (self._line_vao, self._tri_vao):
            try:
                if vao is not None:
                    vao.release()
            except Exception:
                pass
        for vbo in (self._line_vbo, self._tri_vbo):
            try:
                if vbo is not None:
                    vbo.release()
            except Exception:
                pass


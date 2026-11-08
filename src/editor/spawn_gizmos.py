# @heaven3d-part editor/spawn_gizmos.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 12b2 —  SPAWN-POINT GIZMOS  (editor-only debug outlines)
# #############################################################################

class SpawnGizmos:
    """Unity-style scene-view gizmos for every spawn point in the scene.

    * Player Spawn  → full FPS-controller CAPSULE outline (radius 0.35,
                      height 1.8 — the exact Unity / Blender player size)
                      + a facing arrow driven by the node's Yaw.
    * Enemy Spawn   → soccer-ball sphere outline (three great circles,
                      radius 0.11 m) around the enemy's body mesh.
    * Pickups       → half-soccer-ball cube outline (0.11 m edges).

    Drawn for ALL spawn nodes at EDITOR TIME only — during Play they vanish
    exactly like Unity's gizmos.  Lines are rendered on top of the scene
    (no depth test) so the marker can never be lost inside level geometry.
    """

    _SEGMENTS = 48
    _MAX_VERTS = 16384

    # Player capsule: Unity CharacterController / Blender human size.
    PLAYER_RADIUS = 0.35
    PLAYER_HEIGHT = 1.8
    # Regulation football ≈ 22 cm diameter.
    ENEMY_RADIUS = 0.11
    # Half of a soccer ball → 0.11 m cube edges.
    PICKUP_SIZE = 0.11

    COL_PLAYER = (0.20, 1.00, 0.55)
    COL_ENEMY = (1.00, 0.28, 0.20)
    COL_BULLET = (1.00, 0.70, 0.15)
    COL_HEALTH = (0.30, 1.00, 0.45)
    COL_TRIGGER = (0.75, 0.35, 1.00)
    TRIGGER_SIZE = 1.0

    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        self._vbo = ctx.buffer(reserve=self._MAX_VERTS * 6 * 4, dynamic=True)
        self._prog = ShaderLib.get(ctx, "line")
        self._vao = (ctx.vertex_array(
            self._prog, [(self._vbo, "3f 3f", "in_position", "in_color")])
            if self._prog is not None else None)

    # ------------------------------------------------------------ geometry
    def _line(self, out: list, a: glm.vec3, b: glm.vec3, color):
        if (len(out) // 6) + 2 > self._MAX_VERTS:
            return
        out.extend([a.x, a.y, a.z, *color, b.x, b.y, b.z, *color])

    def _arc(self, out: list, center: glm.vec3, u: glm.vec3, v: glm.vec3,
             radius: float, a0: float, a1: float, color):
        if radius <= 1e-5:
            return
        n = self._SEGMENTS
        prev = None
        for i in range(n + 1):
            a = a0 + (a1 - a0) * (i / n)
            p = center + (u * math.cos(a) + v * math.sin(a)) * radius
            if prev is not None:
                self._line(out, prev, p, color)
            prev = p

    def _circle(self, out: list, center: glm.vec3, u: glm.vec3, v: glm.vec3,
                radius: float, color):
        self._arc(out, center, u, v, radius, 0.0, math.tau, color)

    def _build_capsule(self, out: list, node: SceneNode):
        """Full FPS-capsule debug outline: two rim circles, four vertical
        rails and two dome arcs per cap + a yaw facing arrow."""
        c = node.world_position()
        r = self.PLAYER_RADIUS
        cyl_half = self.PLAYER_HEIGHT * 0.5 - r
        col = self.COL_PLAYER
        up = glm.vec3(0, 1, 0)
        # Rim circles at the top/bottom of the cylindrical section.
        self._circle(out, c + up * cyl_half, glm.vec3(1, 0, 0),
                     glm.vec3(0, 0, 1), r, col)
        self._circle(out, c - up * cyl_half, glm.vec3(1, 0, 0),
                     glm.vec3(0, 0, 1), r, col)
        # Feet ring on the ground (reads as the "stand here" marker).
        self._circle(out, c - up * (self.PLAYER_HEIGHT * 0.5),
                     glm.vec3(1, 0, 0), glm.vec3(0, 0, 1), r * 1.15, col)
        # Vertical rails + dome arcs (two perpendicular vertical planes).
        for ux, uz in ((glm.vec3(1, 0, 0), glm.vec3(0, 0, 1)),
                       (glm.vec3(0, 0, 1), glm.vec3(1, 0, 0))):
            for s in (1.0, -1.0):
                side = ux * (r * s)
                self._line(out, c + side - up * cyl_half,
                           c + side + up * cyl_half, col)
            # Top cap: arc from rim up over the pole.
            self._arc(out, c + up * cyl_half, ux, up, r,
                      0.0, math.pi, col)
            # Bottom cap: arc from rim down under the pole.
            self._arc(out, c - up * cyl_half, ux, -up, r,
                      0.0, math.pi, col)
        # Facing arrow at eye height (yaw from the node's rotation).
        yaw = math.radians(float(node.rotation_euler.y))
        fwd = glm.vec3(-math.sin(yaw), 0.0, -math.cos(yaw))
        eye = c + up * (self.PLAYER_HEIGHT * 0.5 - 0.18)
        tip = eye + fwd * 0.55
        self._line(out, eye, tip, col)
        right = glm.vec3(-fwd.z, 0.0, fwd.x)
        self._line(out, tip, tip - fwd * 0.14 + right * 0.07, col)
        self._line(out, tip, tip - fwd * 0.14 - right * 0.07, col)

    def _build_sphere(self, out: list, node: SceneNode):
        c = node.world_position()
        ws = _world_scale(node)
        r = self.ENEMY_RADIUS * max(ws.x, ws.y, ws.z)
        col = self.COL_ENEMY
        self._circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 1, 0), r, col)
        self._circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 0, 1), r, col)
        self._circle(out, c, glm.vec3(0, 1, 0), glm.vec3(0, 0, 1), r, col)

    def _build_cube(self, out: list, node: SceneNode, color,
                    size: float = None):
        c = node.world_position()
        ws = _world_scale(node)
        s = (self.PICKUP_SIZE if size is None else size) * 0.5
        hx, hy, hz = s * ws.x, s * ws.y, s * ws.z
        cmap = {}
        for sx in (1.0, -1.0):
            for sy in (1.0, -1.0):
                for sz in (1.0, -1.0):
                    cmap[(sx, sy, sz)] = c + glm.vec3(sx * hx, sy * hy,
                                                      sz * hz)
        for a, b in (((1,1,1),(-1,1,1)), ((1,1,-1),(-1,1,-1)),
                     ((1,-1,1),(-1,-1,1)), ((1,-1,-1),(-1,-1,-1)),
                     ((1,1,1),(1,-1,1)), ((1,1,-1),(1,-1,-1)),
                     ((-1,1,1),(-1,-1,1)), ((-1,1,-1),(-1,-1,-1)),
                     ((1,1,1),(1,1,-1)), ((1,-1,1),(1,-1,-1)),
                     ((-1,1,1),(-1,1,-1)), ((-1,-1,1),(-1,-1,-1))):
            self._line(out, cmap[a], cmap[b], color)

    # --------------------------------------------------------------- render
    def render(self, camera: Camera, scene: Scene):
        """Draw outlines for EVERY spawn node — edit-time only (the engine
        simply never calls this during Play)."""
        if self._vao is None:
            return
        out: List[float] = []
        for node in scene.root.traverse():
            if not node.visible:
                continue
            kind = getattr(node, "spawn_kind", None)
            if kind == "player":
                self._build_capsule(out, node)
            elif kind == "enemy":
                self._build_sphere(out, node)
            elif kind == "bullet_pickup":
                self._build_cube(out, node, self.COL_BULLET)
            elif kind == "health_pickup":
                self._build_cube(out, node, self.COL_HEALTH)
            elif kind == "trigger":
                self._build_cube(out, node, self.COL_TRIGGER,
                                 size=self.TRIGGER_SIZE)
        count = len(out) // 6
        if count == 0:
            return
        self._vbo.write(np.asarray(out, dtype=np.float32).tobytes())
        self._prog["u_view"].write(camera.view().to_bytes())
        self._prog["u_proj"].write(camera.projection().to_bytes())
        self._prog["u_model"].write(glm.mat4(1.0).to_bytes())
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.BLEND)
        self._vao.render(moderngl.LINES, vertices=count)
        self.ctx.enable(moderngl.DEPTH_TEST)

    def release(self):
        try: self._vao.release()
        except Exception: pass
        try: self._vbo.release()
        except Exception: pass


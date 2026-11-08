# @heaven3d-part editor/light_gizmos.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 12b —  LIGHT GIZMOS  (Unity-style editor debug outlines)
# #############################################################################

class LightGizmos:
    """Unity-style debug outlines for local lights, drawn at EDITOR TIME only
    while a light node is selected.

    * Point light  → three wireframe great circles (XY / XZ / YZ planes) at
                     `range`, centred on the light — Unity's radius sphere.
    * Spot light   → outer-cone base circle at `range` + four apex-to-rim
                     generator lines, plus a dimmer inner-angle circle that
                     shows where the soft penumbra begins.

    The outlines follow the light's own colour (hue-normalised so dark lights
    still read as vivid outlines), are drawn on top of the scene (no depth
    test) exactly like Unity's scene-view gizmos, and never appear at runtime
    or during Play.  GL_LINES is fine here — an outline only needs 1px lines,
    unlike the interactive transform gizmo which must be thick and clickable.
    """

    _SEGMENTS = 64          # line segments per circle
    _MAX_VERTS = 8192       # reserved dynamic-VBO capacity (plenty of lights)

    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        # Interleaved (px,py,pz, cr,cg,cb) — matches the shared "line" shader.
        self._vbo = ctx.buffer(reserve=self._MAX_VERTS * 6 * 4, dynamic=True)
        self._prog = ShaderLib.get(ctx, "line")
        self._vao = (ctx.vertex_array(
            self._prog, [(self._vbo, "3f 3f", "in_position", "in_color")])
            if self._prog is not None else None)

    # ------------------------------------------------------------ geometry
    @staticmethod
    def _tint(color: glm.vec3) -> Tuple[float, float, float]:
        """Hue-preserving gizmo colour: normalise to the strongest channel so
        the outline stays bright regardless of light intensity (Unity tints
        gizmos by the light colour but keeps them vivid)."""
        r, g, b = float(color.x), float(color.y), float(color.z)
        m = max(r, g, b)
        if m <= 1e-4:
            return (1.0, 0.9, 0.4)   # Unity's default gizmo amber
        return (min(r / m, 1.0), min(g / m, 1.0), min(b / m, 1.0))

    @classmethod
    def _circle(cls, out: list, center: glm.vec3, u: glm.vec3, v: glm.vec3,
                radius: float, color, cap: int):
        """Append a closed line-strip circle in the plane spanned by (u, v)."""
        if radius <= 1e-4:
            return
        n = cls._SEGMENTS
        if (len(out) // 6) + n * 2 > cap:
            return
        prev = None
        for i in range(n + 1):
            a = (i % n) / n * math.tau
            p = center + (u * math.cos(a) + v * math.sin(a)) * radius
            if prev is not None:
                out.extend([prev.x, prev.y, prev.z, *color,
                            p.x, p.y, p.z, *color])
            prev = p

    @staticmethod
    def _line(out: list, a: glm.vec3, b: glm.vec3, color, cap: int):
        if (len(out) // 6) + 2 > cap:
            return
        out.extend([a.x, a.y, a.z, *color, b.x, b.y, b.z, *color])

    def _build_point(self, out: list, node: SceneNode, light: "PointLight"):
        c = node.world_position()
        r = max(0.01, float(light.range))
        col = self._tint(light.color)
        cap = self._MAX_VERTS
        # Three orthogonal great circles — Unity's point-light radius sphere.
        self._circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 1, 0), r, col, cap)
        self._circle(out, c, glm.vec3(1, 0, 0), glm.vec3(0, 0, 1), r, col, cap)
        self._circle(out, c, glm.vec3(0, 1, 0), glm.vec3(0, 0, 1), r, col, cap)

    def _build_spot(self, out: list, node: SceneNode, light: "SpotLight"):
        apex = node.world_position()
        d = Scene.forward_from_rotation(node.rotation_euler)
        rng = max(0.01, float(light.range))
        outer = _clamp(float(light.outer_angle), 0.1, 179.0)
        inner = _clamp(float(light.inner_angle), 0.1, outer)
        col = self._tint(light.color)
        dim = tuple(c * 0.55 for c in col)
        cap = self._MAX_VERTS
        # Orthonormal basis around the cone axis.
        ref = glm.vec3(0, 1, 0) if abs(d.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(d, ref))
        v = glm.cross(d, u)
        # Unity exposes FULL cone angles, so the rim radius uses the half-angle.
        base = apex + d * rng
        r_out = math.tan(math.radians(outer * 0.5)) * rng
        r_in = math.tan(math.radians(inner * 0.5)) * rng
        # Outer cone: base circle + four apex→rim generator lines.
        self._circle(out, base, u, v, r_out, col, cap)
        for k in range(4):
            a = k * math.pi * 0.5
            rim = base + (u * math.cos(a) + v * math.sin(a)) * r_out
            self._line(out, apex, rim, col, cap)
        # Inner cone: dimmer circle marking the fully-lit core.
        if 1e-4 < r_in < r_out - 1e-4:
            self._circle(out, base, u, v, r_in, dim, cap)

    # --------------------------------------------------------------- render
    def render(self, camera: Camera, selected: Optional[SceneNode]):
        """Draw outlines for every light in the SELECTED node's subtree.
        Called once per editor frame; a no-op when no light is selected."""
        if self._vao is None or selected is None:
            return
        out: List[float] = []
        for node in selected.traverse():
            if not node.visible:
                continue
            if node.is_point_light and node.point_light is not None:
                self._build_point(out, node, node.point_light)
            elif node.is_spot_light and node.spot_light is not None:
                self._build_spot(out, node, node.spot_light)
        count = len(out) // 6
        if count == 0:
            return
        self._vbo.write(np.asarray(out, dtype=np.float32).tobytes())
        self._prog["u_view"].write(camera.view().to_bytes())
        self._prog["u_proj"].write(camera.projection().to_bytes())
        self._prog["u_model"].write(glm.mat4(1.0).to_bytes())
        # Always on top of the scene, like Unity's scene-view gizmos.
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.BLEND)
        self._vao.render(moderngl.LINES, vertices=count)
        self.ctx.enable(moderngl.DEPTH_TEST)

    def release(self):
        try: self._vao.release()
        except Exception: pass
        try: self._vbo.release()
        except Exception: pass


# @heaven3d-part editor/view_gizmo.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 12d —  VIEW GIZMO  (Blender-style viewport navigation ball)
# #############################################################################

# Self-contained shaders for the view gizmo — smooth-lit "plastic" shading
# (key light + hemispheric bounce + specular glint) so the axis balls read
# as 3D spheres exactly like Blender's navigation gizmo.
VIEWGIZMO_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec3 in_color;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec3 v_color;
out vec3 v_normal;
void main(){
    gl_Position = u_proj * u_view * vec4(in_position, 1.0);
    v_normal = in_normal;
    v_color = in_color;
}
"""
VIEWGIZMO_FRAG = """#version 330 core
in vec3 v_color;
in vec3 v_normal;
uniform float u_tint;      // hover highlight multiplier (1.0 = normal)
out vec4 frag_color;
void main(){
    vec3 n = normalize(v_normal);
    vec3 l = normalize(vec3(-0.45, 0.75, 0.55));   // key light, gizmo space
    float diff = max(dot(n, l), 0.0);
    float hemi = 0.5 + 0.5 * n.y;                  // cheap sky bounce
    vec3 col = v_color * (0.35 + 0.50 * diff + 0.20 * hemi);
    vec3 h = normalize(l + vec3(0.0, 0.0, 1.0));   // specular glint
    col += vec3(0.30) * pow(max(dot(n, h), 0.0), 42.0);
    frag_color = vec4(min(col * u_tint, vec3(1.0)), 1.0);
}
"""
# Blit shader — composites the gizmo's offscreen texture onto the screen
# with straight alpha blending (the offscreen buffer is cleared to alpha 0,
# so only the navball geometry lands on the 3D view).
VIEWGIZMO_BLIT_VERT = """#version 330 core
layout (location = 0) in vec2 in_pos;    // unit quad 0..1
uniform vec4 u_rect;                     // px: x, y, w, h (GL bottom-origin)
uniform vec2 u_screen;                   // px: window w, h
out vec2 v_uv;
void main(){
    vec2 px = u_rect.xy + in_pos * u_rect.zw;
    vec2 ndc = px / u_screen * 2.0 - 1.0;
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = in_pos;
}
"""
VIEWGIZMO_BLIT_FRAG = """#version 330 core
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 frag_color;
void main(){
    frag_color = texture(u_tex, v_uv);
}
"""


class ViewGizmo:
    """
    Blender-style view gizmo (the "navball" in the top-right corner of
    Blender's 3D viewport) — an EDIT-TIME-ONLY overlay:

        * three shaded axis balls — red X, green Y, blue Z — that rotate
          together with the editor camera (orientation mirror);
        * three muted rings on the negative ends of the axes;
        * hovering an axis highlights it;
        * CLICKING an axis ball (or its opposite ring) smoothly snaps the
          camera to look straight down that axis (front / right / top …);
        * DRAGGING anywhere on the gizmo orbits the view around the pivot.

    Visibility contract (identical to the editor grid / light gizmos):
    the engine only creates this in ``mode == "editor"`` and only renders
    it while ``not self._playing``.  It never exists in the standalone
    runtime build.
    """

    # Blender's axis palette (negative ends are drawn as grey rings).
    AXIS_COLORS = {
        "x": (0.925, 0.250, 0.275),
        "y": (0.545, 0.800, 0.155),
        "z": (0.235, 0.465, 0.905),
    }
    RING_COLOR = (0.46, 0.46, 0.50)
    AXES = {
        "x": glm.vec3(1, 0, 0),
        "y": glm.vec3(0, 1, 0),
        "z": glm.vec3(0, 0, 1),
    }

    # Unit-gizmo-space layout (the visible ortho box is ±_HALF).
    _BALL_DIST = 0.80      # ball / ring centre distance from the origin
    _BALL_R = 0.26
    _RING_MAJOR = 0.19
    _RING_TUBE = 0.035
    _STICK_LEN = 0.60
    _STICK_R = 0.045
    _HALF = 1.30

    # Screen placement (pixels), top-right corner of the 3D viewport.  The
    # fractions mirror _build_ui(): inspector starts at x=0.82, the navbar
    # sits above y=0.93.
    _SIZE_PX = 130  # [FINALPATCH3:ui_scale_120]
    _MARGIN_PX = 14  # [FINALPATCH3:ui_scale_120]
    _VIEWPORT_RIGHT = 0.82
    _VIEWPORT_TOP = 0.93

    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        self.hovered: Optional[str] = None       # "x" / "-x" / ... / None
        self.pressing = False                    # LMB held inside the gizmo
        self._press_part: Optional[str] = None
        self._press_pos = (0, 0)
        self._orbiting = False
        self._snap_anim: Optional[dict] = None

        # ---- merged geometry: (pos3, normal3, color3) per vertex ----
        verts: List[float] = []
        self._ranges: List[Tuple[str, int, int]] = []
        for name, axis in self.AXES.items():
            col = self.AXIS_COLORS[name]
            self._ranges.append(self._cylinder(
                axis, glm.vec3(0), self._STICK_LEN, self._STICK_R,
                col, 12, f"stick_{name}", verts))
            self._ranges.append(self._sphere(
                axis * self._BALL_DIST, self._BALL_R, col,
                18, 24, f"ball_{name}", verts))
            self._ranges.append(self._torus(
                axis, -axis * self._BALL_DIST, self._RING_MAJOR,
                self._RING_TUBE, self.RING_COLOR, 36, 8, f"ring_{name}", verts))
        data = np.array(verts, dtype=np.float32)
        self._vbo = ctx.buffer(data.tobytes())
        self._prog = ctx.program(vertex_shader=VIEWGIZMO_VERT,
                                 fragment_shader=VIEWGIZMO_FRAG)
        self._vao = ctx.vertex_array(
            self._prog,
            [(self._vbo, "3f 3f 3f", "in_position", "in_normal", "in_color")])

        # ---- offscreen target the size of the gizmo square ----
        # Rendering the navball into its OWN framebuffer (with its own depth
        # buffer) gives perfect 3D occlusion inside the gizmo and touches no
        # screen state whatsoever — the result is then alpha-composited onto
        # the viewport corner.  (A masked depth-clear directly on the screen
        # proved unreliable: some drivers ignore the colour-mask restore and
        # punch a hole in the scene.  The offscreen pass cannot fail that way.)
        s = self._SIZE_PX
        self._color_tex = ctx.texture((s, s), 4)
        self._color_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._depth_rb = ctx.depth_renderbuffer((s, s))
        self._fbo = ctx.framebuffer([self._color_tex], self._depth_rb)
        self._blit_prog = ctx.program(vertex_shader=VIEWGIZMO_BLIT_VERT,
                                      fragment_shader=VIEWGIZMO_BLIT_FRAG)
        quad = np.array([0, 0,  1, 0,  1, 1,
                         0, 0,  1, 1,  0, 1], dtype=np.float32)
        self._quad_vbo = ctx.buffer(quad.tobytes())
        self._quad_vao = ctx.vertex_array(
            self._blit_prog, [(self._quad_vbo, "2f", "in_pos")])

    # ============================================================ geometry
    @staticmethod
    def _emit(out, p, n, c):
        out.extend([p.x, p.y, p.z, n.x, n.y, n.z, c[0], c[1], c[2]])

    @classmethod
    def _basis(cls, axis):
        a = glm.normalize(axis)
        ref = glm.vec3(0, 1, 0) if abs(a.y) < 0.9 else glm.vec3(1, 0, 0)
        u = glm.normalize(glm.cross(a, ref))
        return a, u, glm.cross(a, u)

    @classmethod
    def _cylinder(cls, axis, p0, length, radius, color, segments,
                  tag, out):
        start = len(out) // 9
        a, u, v = cls._basis(axis)
        ring0, ring1 = [], []
        for i in range(segments):
            ang = (i / segments) * math.pi * 2
            off = (u * math.cos(ang) + v * math.sin(ang))
            ring0.append((p0 + off * radius, off))
            ring1.append((p0 + a * length + off * radius, off))
        for i in range(segments):
            j = (i + 1) % segments
            for p, n in (ring0[i], ring0[j], ring1[i],
                         ring1[i], ring0[j], ring1[j]):
                cls._emit(out, p, n, color)
        return (tag, start, (len(out) // 9) - start)

    @classmethod
    def _sphere(cls, center, radius, color, stacks, slices, tag, out):
        start = len(out) // 9
        grid = []
        for i in range(stacks + 1):
            phi = math.pi * i / stacks          # 0 (top) .. pi (bottom)
            row = []
            for j in range(slices + 1):
                theta = 2.0 * math.pi * j / slices
                n = glm.vec3(math.sin(phi) * math.cos(theta),
                             math.cos(phi),
                             math.sin(phi) * math.sin(theta))
                row.append((center + n * radius, n))
            grid.append(row)
        for i in range(stacks):
            for j in range(slices):
                for p, n in (grid[i][j], grid[i + 1][j], grid[i][j + 1],
                             grid[i][j + 1], grid[i + 1][j], grid[i + 1][j + 1]):
                    cls._emit(out, p, n, color)
        return (tag, start, (len(out) // 9) - start)

    @classmethod
    def _torus(cls, axis, center, major, tube, color, major_seg, minor_seg,
               tag, out):
        start = len(out) // 9
        a, u, v = cls._basis(axis)
        grid = []
        for i in range(major_seg):
            ma = (i / major_seg) * math.pi * 2
            radial = u * math.cos(ma) + v * math.sin(ma)
            rc = center + radial * major
            row = []
            for j in range(minor_seg):
                mi = (j / minor_seg) * math.pi * 2
                n = glm.normalize(radial * math.cos(mi) + a * math.sin(mi))
                row.append((rc + n * tube, n))
            grid.append(row)
        for i in range(major_seg):
            i2 = (i + 1) % major_seg
            for j in range(minor_seg):
                j2 = (j + 1) % minor_seg
                for p, n in (grid[i][j], grid[i2][j], grid[i][j2],
                             grid[i][j2], grid[i2][j], grid[i2][j2]):
                    cls._emit(out, p, n, color)
        return (tag, start, (len(out) // 9) - start)

    # ======================================================= screen layout
    @classmethod
    def region_px(cls, win_w: int, win_h: int) -> Tuple[int, int, int]:
        """(x, y, size) of the gizmo square — GL BOTTOM-origin pixels."""
        s = cls._SIZE_PX
        gx = int(cls._VIEWPORT_RIGHT * win_w) - cls._MARGIN_PX - s
        gy = int(cls._VIEWPORT_TOP * win_h) - cls._MARGIN_PX - s
        return max(0, gx), max(0, gy), s

    @classmethod
    def _view_rot(cls, camera: Camera) -> glm.mat4:
        # Rotation part of the camera view matrix only — the gizmo floats
        # at the origin of its own little world.
        return glm.mat4(glm.mat3(camera.view()))

    @classmethod
    def _proj(cls) -> glm.mat4:
        h = cls._HALF
        return glm.ortho(-h, h, -h, h, -2.5, 2.5)

    @classmethod
    def _project_ndc(cls, camera: Camera, p: glm.vec3) -> glm.vec3:
        v = cls._proj() * cls._view_rot(camera) * glm.vec4(p, 1.0)
        return glm.vec3(v.x, v.y, v.z)

    @classmethod
    def _project_px(cls, camera, p, win_w, win_h) -> Tuple[float, float]:
        ndc = cls._project_ndc(camera, p)
        gx, gy, s = cls.region_px(win_w, win_h)
        return (gx + (ndc.x * 0.5 + 0.5) * s,
                gy + (ndc.y * 0.5 + 0.5) * s)

    @classmethod
    def _part_points(cls):
        # Balls first so they win hit-test ties against the rings.
        out = [(name, axis * cls._BALL_DIST, "ball")
               for name, axis in cls.AXES.items()]
        out += [("-" + name, -axis * cls._BALL_DIST, "ring")
                for name, axis in cls.AXES.items()]
        return out

    # ========================================================== interaction
    def point_in_region(self, mouse_pos, win_w, win_h) -> bool:
        """mouse_pos is pygame's TOP-origin pixel position."""
        mx, my = mouse_pos
        my = win_h - my                       # → GL bottom-origin
        gx, gy, s = self.region_px(win_w, win_h)
        return gx <= mx <= gx + s and gy <= my <= gy + s

    def hit_test(self, mouse_pos, win_w, win_h, camera) -> Optional[str]:
        """Which axis part is under the cursor: "x", "-x", ... or None."""
        if not self.point_in_region(mouse_pos, win_w, win_h):
            return None
        mx, my = mouse_pos
        my = win_h - my
        gx, gy, s = self.region_px(win_w, win_h)
        best, best_d = None, float("inf")
        for label, pt, kind in self._part_points():
            ndc = self._project_ndc(camera, pt)
            px = gx + (ndc.x * 0.5 + 0.5) * s
            py = gy + (ndc.y * 0.5 + 0.5) * s
            d = math.hypot(mx - px, my - py)
            visual_r = self._BALL_R if kind == "ball" else self._RING_MAJOR
            radius = visual_r / self._HALF * (s * 0.5) + 5.0
            if d <= radius and d < best_d:
                best, best_d = label, d
        return best

    def update_hover(self, mouse_pos, win_w, win_h, camera):
        self.hovered = self.hit_test(mouse_pos, win_w, win_h, camera)

    def begin_press(self, part: Optional[str], mouse_pos):
        self.pressing = True
        self._press_part = part
        self._press_pos = tuple(mouse_pos)
        self._orbiting = False

    def update_press(self, mouse_pos, mouse_delta, camera: Camera):
        """Dragging the gizmo orbits the view (Blender behaviour); a clean
        click without motion snaps on release (see end_press)."""
        if not self.pressing:
            return
        if not self._orbiting:
            if math.hypot(mouse_pos[0] - self._press_pos[0],
                          mouse_pos[1] - self._press_pos[1]) > 4.0:
                self._orbiting = True
                self._snap_anim = None      # user input cancels any snap
        if self._orbiting:
            dx, dy = mouse_delta
            camera.yaw -= dx * camera.look_speed * 0.9
            camera.pitch = _clamp(camera.pitch - dy * camera.look_speed * 0.9,
                                  -89.9, 89.9)
            camera._orbit_from_pivot()

    def end_press(self, camera: Camera):
        if self.pressing and not self._orbiting and self._press_part:
            self.snap_to_axis(camera, self._press_part)
        self.pressing = False
        self._press_part = None
        self._orbiting = False

    def snap_to_axis(self, camera: Camera, part: str):
        """Blender-style view snap: clicking +X looks along -X (camera sits
        on the +X side of the pivot), the -X ring flips it, and so on.
        The transition is eased over ~0.22 s like Blender's view animation."""
        name = part.lstrip("-")
        sign = -1.0 if part.startswith("-") else 1.0
        f = -(self.AXES[name] * sign)            # camera forward after snap
        pitch = math.degrees(math.asin(_clamp(f.y, -1.0, 1.0)))
        yaw = camera.yaw
        if abs(f.x) > 1e-6 or abs(f.z) > 1e-6:
            yaw = math.degrees(math.atan2(-f.x, -f.z))
        # ±90° would degenerate the engine's camera basis (right = fwd×up),
        # so top/bottom snaps stop a hair short — visually identical.
        pitch = _clamp(pitch, -89.9, 89.9)
        dyaw = (yaw - camera.yaw + 180.0) % 360.0 - 180.0
        self._snap_anim = {"t": 0.0, "dur": 0.22,
                           "yaw0": camera.yaw, "dyaw": dyaw,
                           "pitch0": camera.pitch, "pitch1": pitch}

    def update(self, dt: float, camera: Camera):
        """Advance the eased snap animation (called once per editor frame)."""
        a = self._snap_anim
        if a is None:
            return
        a["t"] += dt
        k = min(1.0, a["t"] / a["dur"])
        k = 1.0 - (1.0 - k) ** 3                 # ease-out cubic
        camera.yaw = a["yaw0"] + a["dyaw"] * k
        camera.pitch = a["pitch0"] + (a["pitch1"] - a["pitch0"]) * k
        camera._orbit_from_pivot()
        if k >= 1.0:
            self._snap_anim = None

    # ============================================================== render
    def render(self, camera: Camera, win_w: int, win_h: int):
        gx, gy, s = self.region_px(win_w, win_h)
        ctx = self.ctx

        # ---- pass 1: the navball into its own tiny framebuffer ----
        # NOTE: Context.depth_func is WRITE-ONLY in ModernGL 5.x — reading
        # it raises NotImplementedError.  We therefore never save it; we set
        # "<" for our own pass and restore "<" (the GL default, also what
        # the renderer's geometry pass sets) afterwards.
        self._fbo.use()
        ctx.viewport = (0, 0, s, s)
        ctx.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        ctx.disable(moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        self._prog["u_view"].write(self._view_rot(camera).to_bytes())
        self._prog["u_proj"].write(self._proj().to_bytes())
        for tag, start, count in self._ranges:
            tint = 1.0
            if tag.startswith("ball_") and self.hovered == tag[5:]:
                tint = 1.45
            elif tag.startswith("ring_") and self.hovered == "-" + tag[5:]:
                tint = 1.60
            self._prog["u_tint"] = tint
            self._vao.render(moderngl.TRIANGLES, vertices=count, first=start)

        # ---- pass 2: alpha-composite the result onto the screen corner ----
        ctx.screen.use()
        ctx.viewport = (0, 0, win_w, win_h)
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self._blit_prog["u_rect"] = (float(gx), float(gy), float(s), float(s))
        self._blit_prog["u_screen"] = (float(win_w), float(win_h))
        self._blit_prog["u_tex"] = 0
        self._color_tex.use(location=0)
        self._quad_vao.render(moderngl.TRIANGLES)
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"   # restore the default (getter is write-only —
                               # see the note at the top of this method)

    def emit_labels(self, ui: "UIRenderer", win_w: int, win_h: int,
                    camera: Camera):
        """Queue the white X / Y / Z letters on the axis balls through the
        UI pass (crisp font textures, always on top of the 3D overlay)."""
        lh = 15.0                                # glyph height, px
        for name, axis in self.AXES.items():
            px, py = self._project_px(camera, axis * (self._BALL_DIST + 0.01),
                                      win_w, win_h)
            letter = name.upper()
            tw, th = ui.font.text_size(letter)
            w_px = lh * (tw / max(1, th))
            ui.text(UIRect((px - w_px * 0.5) / win_w,
                           (py - lh * 0.5) / win_h,
                           (w_px + 2.0) / win_w,
                           lh / win_h),
                    letter, (1.0, 1.0, 1.0))

    def release(self):
        for obj in (self._vao, self._vbo, self._prog,
                    self._quad_vao, self._quad_vbo, self._blit_prog,
                    self._fbo, self._color_tex, self._depth_rb):
            try:
                if obj is not None:
                    obj.release()
            except Exception:
                pass


# @heaven3d-part editor/grid.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 10b —  EDITOR GRID  (infinite fading ground grid, editor-only)
# #############################################################################

class EditorGrid:
    """Renders a large ground plane at Y=0 with a fading infinite grid
    drawn procedurally in the fragment shader.  The grid is rendered as
    standard triangle geometry (one big quad) so depth-testing against
    scene geometry works correctly.  Should only be drawn in editor mode
    when NOT playing — the caller is responsible for the guard."""

    def __init__(self, ctx: moderngl.Context,
                 size: float = 2000.0,
                 cell_size: float = 1.0,
                 major_every: float = 10.0,
                 fade_distance: float = 200.0):
        self.ctx = ctx
        self.size = float(size)
        self.cell_size = float(cell_size)
        self.major_every = float(major_every)
        self.fade_distance = float(fade_distance)
        # One large horizontal quad on Y=0
        s = self.size * 0.5
        # 6 verts (two triangles) of vec3 positions
        verts = np.array([
            -s, 0, -s,
             s, 0, -s,
             s, 0,  s,
            -s, 0, -s,
             s, 0,  s,
            -s, 0,  s,
        ], dtype=np.float32)
        self.vbo = ctx.buffer(verts.tobytes())
        prog = ShaderLib.get(ctx, "grid")
        self._prog = prog
        self._vao = None
        if prog is not None:
            self._vao = ctx.vertex_array(prog, [(self.vbo, "3f", "in_position")])

    def render(self, camera: Camera):
        if self._prog is None or self._vao is None:
            return
        self._prog["u_view"].write(camera.view().to_bytes())
        self._prog["u_proj"].write(camera.projection().to_bytes())
        self._prog["u_model"].write(glm.mat4(1.0).to_bytes())
        self._prog["u_camera_pos"].write(camera.position.to_bytes())
        self._prog["u_grid_color_minor"].write(glm.vec3(0.35, 0.35, 0.35).to_bytes())
        self._prog["u_grid_color_major"].write(glm.vec3(0.55, 0.55, 0.55).to_bytes())
        self._prog["u_cell_size"] = self.cell_size
        self._prog["u_major_every"] = self.major_every
        self._prog["u_fade_distance"] = self.fade_distance
        # The grid uses alpha blending (so faded cells composite over the
        # sky / scene) but writes/tests depth so it sits on the ground.
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA,
                               moderngl.ONE_MINUS_SRC_ALPHA)
        self.ctx.enable(moderngl.DEPTH_TEST)
        # Disable depth WRITE so the grid plane doesn't occlude scene geometry
        # drawn afterwards; only test against existing depth.
        try:
            self.ctx.depth_mask = False
        except Exception:
            pass
        self._vao.render(moderngl.TRIANGLES)
        try:
            self.ctx.depth_mask = True
        except Exception:
            pass

    def release(self):
        try: self._vao.release()
        except Exception: pass
        try: self.vbo.release()
        except Exception: pass


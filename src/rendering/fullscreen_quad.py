# @heaven3d-part rendering/fullscreen_quad.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 10 —  FULLSCREEN QUAD  (shared VAO for post-processing)
# #############################################################################

class FullscreenQuad:
    """A 3-vertex fullscreen triangle VAO usable with any fullscreen program."""
    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        # Empty VBO; the vertex shader uses gl_VertexID
        self.vbo = ctx.buffer(b"\x00" * 12)
        self._vaos: Dict[int, moderngl.VertexArray] = {}

    def render(self, program: moderngl.Program):
        pid = id(program)
        if pid not in self._vaos:
            try:
                self._vaos[pid] = self.ctx.vertex_array(program, [(self.vbo, "3f", "in_position")])
            except Exception:
                # program has no in_position attribute (fullscreen uses gl_VertexID)
                self._vaos[pid] = self.ctx.vertex_array(program, [])
        self._vaos[pid].render(moderngl.TRIANGLES, vertices=3)

    def release(self):
        for v in self._vaos.values():
            try: v.release()
            except Exception: pass
        self._vaos.clear()
        try: self.vbo.release()
        except Exception: pass


# @heaven3d-part h3d27/engine/10_view_menu.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
    # ---------------------------------------------------------------
    # 10. VIEW MENU (viewport top-left "View" button, not in the main bar)
    #     Shading: Lit / Lit + Wireframe / Wireframe (x-ray), grid on/off.
    #     Wireframes are drawn into a private framebuffer and composited, so
    #     no engine GL state is disturbed (same approach as the outline).
    # ---------------------------------------------------------------
    _H3D27_WIRE_VERT = """#version 330 core
in vec3 in_position;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(in_position, 1.0); }
"""
    _H3D27_WIRE_FRAG = """#version 330 core
uniform vec4 u_color;
out vec4 frag;
void main() { frag = u_color; }
"""
    _H3D27_BLIT_VERT = """#version 330 core
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""
    _H3D27_BLIT_FRAG = """#version 330 core
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 frag;
void main() { frag = texture(u_tex, v_uv); }
"""
    SHADING_MODES = (("lit", "Lit"), ("lit_wire", "Lit + Wireframe"), ("wire", "Wireframe"))

    def _h3d27_view(self):
        st = self.__dict__.get("_h3d27_view_state")
        if st is None:
            st = {"shading": "lit", "grid": True, "menu": False}
            self.__dict__["_h3d27_view_state"] = st
        return st

    def set_shading(self, mode):
        """Editor viewport shading: "lit", "lit_wire" or "wire"."""
        if mode not in [m for m, _ in SHADING_MODES]:
            raise ValueError(mode)
        _h3d27_view(self)["shading"] = mode

    def set_grid_visible(self, on):
        st = _h3d27_view(self)
        st["grid"] = bool(on)
        if on and getattr(self, "_h3d27_grid_saved", None) is not None and self.editor_grid is None:
            self.editor_grid = self._h3d27_grid_saved
        elif not on and self.editor_grid is not None:
            self._h3d27_grid_saved = self.editor_grid
            self.editor_grid = None

    HeavenEngine.set_shading = set_shading
    HeavenEngine.set_grid_visible = set_grid_visible

    def _h3d27_draw_wire(self):
        st = _h3d27_view(self)
        mode = st["shading"]
        if mode == "lit" or getattr(self, "mode", None) != "editor":
            return
        ctx = self.ctx
        res = self.__dict__.get("_h3d27_wire_res")
        if res is None:
            try:
                prog = ctx.program(vertex_shader=_H3D27_WIRE_VERT, fragment_shader=_H3D27_WIRE_FRAG)
                blit = ctx.program(vertex_shader=_H3D27_BLIT_VERT, fragment_shader=_H3D27_BLIT_FRAG)
                res = {"prog": prog, "blit": blit, "blit_vao": ctx.vertex_array(blit, []), "fbo": None}
            except Exception as ex:
                _h3d27_log_once("wire_prog", f"[View] wireframe shaders failed: {ex}")
                res = False
            self.__dict__["_h3d27_wire_res"] = res
        if not res:
            return
        w, h = int(self.width), int(self.height)
        if res["fbo"] is None or res["size"] != (w, h):
            for k in ("fbo", "tex", "depth"):
                try:
                    res[k].release()
                except Exception:
                    pass
            res["tex"] = ctx.texture((w, h), 4)
            res["depth"] = ctx.depth_renderbuffer((w, h))
            res["fbo"] = ctx.framebuffer([res["tex"]], res["depth"])
            res["size"] = (w, h)
        prog = res["prog"]
        vp = self.camera.projection() * self.camera.view()
        sel = getattr(self, "selected", None)
        sel_ids = {id(n) for n in sel.traverse()} if sel is not None else set()
        items = []
        for node in self.scene.root.traverse():
            mesh = getattr(node, "mesh", None)
            if mesh is None or getattr(mesh, "vbo", None) is None or not getattr(node, "visible", True):
                continue
            if node.name.startswith("__"):
                continue
            try:
                items.append((mesh, vp * node.world_matrix(), id(node) in sel_ids))
            except Exception:
                continue

        def draw(color_fn):
            for mesh, mvp, is_sel in items:
                try:
                    prog["u_mvp"].write(mvp.to_bytes())
                    prog["u_color"].value = color_fn(is_sel)
                    vao = mesh.get_vao(prog)
                    if getattr(mesh, "indexed", False):
                        vao.render(moderngl.TRIANGLES)
                    else:
                        vao.render(moderngl.TRIANGLES, vertices=mesh.vertex_count)
                except Exception:
                    continue
        try:
            fbo = res["fbo"]
            fbo.use()
            ctx.viewport = (0, 0, w, h)
            ctx.disable(moderngl.CULL_FACE)
            ctx.disable(moderngl.BLEND)
            if mode == "wire":
                fbo.clear(0.135, 0.145, 0.165, 1.0, depth=1.0)
                ctx.disable(moderngl.DEPTH_TEST)
                ctx.wireframe = True
                draw(lambda s: (1.0, 0.62, 0.18, 1.0) if s else (0.72, 0.76, 0.84, 1.0))
            else:
                fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
                ctx.enable(moderngl.DEPTH_TEST)
                ctx.depth_func = "<="
                # depth prepass, pushed back a little so edges win the depth test
                ctx.enable(moderngl.BLEND)
                ctx.blend_func = (moderngl.ZERO, moderngl.ONE)
                ctx.polygon_offset = (1.0, 1.0)
                draw(lambda s: (0.0, 0.0, 0.0, 0.0))
                ctx.polygon_offset = (0.0, 0.0)
                ctx.disable(moderngl.BLEND)
                ctx.wireframe = True
                draw(lambda s: (1.0, 0.62, 0.18, 0.95) if s else (0.04, 0.05, 0.07, 0.75))
            ctx.wireframe = False
            ctx.screen.use()
            ctx.viewport = (0, 0, w, h)
            ctx.disable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
            res["tex"].use(location=0)
            res["blit"]["u_tex"] = 0
            res["blit_vao"].render(moderngl.TRIANGLES, vertices=3)
        except Exception as ex:
            _h3d27_log_once("wire_draw", f"[View] wireframe draw failed: {ex}")
        finally:
            try:
                ctx.wireframe = False
                ctx.polygon_offset = (0.0, 0.0)
                ctx.screen.use()
                ctx.disable(moderngl.BLEND)
                ctx.enable(moderngl.DEPTH_TEST)
                ctx.depth_func = "<"
                ctx.depth_mask = True
            except Exception:
                pass

    _H3D27_PREV_OUTLINE2 = HeavenEngine._render_selection_outline

    def _h3d27_outline2(self):
        try:
            _h3d27_draw_wire(self)                # under the selection outline + gizmos
        except Exception:
            pass
        return _H3D27_PREV_OUTLINE2(self)

    HeavenEngine._render_selection_outline = _h3d27_outline2

    _H3D27_PREV_BUILD_UI2 = HeavenEngine._build_ui

    def _h3d27_build_ui2(self):
        _H3D27_PREV_BUILD_UI2(self)
        try:
            if self.ui is None or self.mode != "editor":
                return
            st = _h3d27_view(self)
            T = THEME
            cur = dict(SHADING_MODES)[st["shading"]]
            x, y = 0.186, 0.884
            self.ui.button(UIRect(x, y, 0.085, 0.032), f"View: {cur}",
                           lambda: st.update(menu=not st["menu"]),
                           T["accent_dim"] if st["menu"] else T["panel_dark"], align="left", pad_x_px=10)
            if not st["menu"]:
                return
            rows = [(f"{'●' if st['shading'] == m else '○'}  {label}", (lambda m=m: (set_shading(self, m), st.update(menu=False))))
                    for m, label in SHADING_MODES]
            rows.append((f"{'☑' if st['grid'] else '☐'}  Show grid",
                         lambda: (set_grid_visible(self, not st["grid"]), st.update(menu=False))))
            h = 0.032
            height = len(rows) * (h + 0.005) + 0.012
            top = y - 0.006
            self.ui.panel(UIRect(x, top - height, 0.15, height), T["panel"])
            yy = top - 0.006 - h
            for label, fn in rows:
                self.ui.button(UIRect(x + 0.005, yy, 0.14, h), label, fn, T["button"], align="left", pad_x_px=10)
                yy -= h + 0.005
        except Exception as ex:
            _h3d27_log_once("view_menu", f"[View] menu: {ex}")

    HeavenEngine._build_ui = _h3d27_build_ui2

# @heaven3d-part editor/selection_outline.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# TRIPLE-A PATCH BLOCK p3_editor — shared runtime code
# [TRIPLEA:p3_runtime_block]
# Rotation-composition helpers, triangle-accurate picking,
# Unity-style selection outline, and animated GIF textures.
# #############################################################################


def _quat_to_yxz_euler_deg(q) -> "glm.vec3":
    """Quaternion -> euler DEGREES in the engine's Unity-style Y-X-Z
    convention (R = Ry(y) * Rx(x) * Rz(z), see SceneNode.local_matrix).

    Extracted from the rotation matrix so the euler order is explicit —
    glm.eulerAngles() uses XYZ order and silently corrupts any code that
    feeds its result into Y-X-Z composition."""
    m = glm.mat3_cast(glm.normalize(q))
    # pyglm matrices are column-major: m[c][r].  For R = Ry*Rx*Rz:
    #   M[1][2] = -sin(x)          M[0][2] = sin(y)*cos(x)
    #   M[2][2] =  cos(y)*cos(x)   M[1][0] = cos(x)*sin(z)
    #   M[1][1] =  cos(x)*cos(z)
    sx = max(-1.0, min(1.0, -float(m[2][1])))
    x = math.asin(sx)
    if abs(math.cos(x)) > 1e-6:
        y = math.atan2(float(m[2][0]), float(m[2][2]))
        z = math.atan2(float(m[0][1]), float(m[1][1]))
    else:
        # Gimbal lock (x ~= +/-90 deg): y and z are coupled — keep y, zero z.
        y = math.atan2(-float(m[0][2]), float(m[0][0]))
        z = 0.0
    return glm.vec3(math.degrees(x), math.degrees(y), math.degrees(z))


def _yxz_euler_deg_to_quat(e) -> "glm.quat":
    """Inverse of _quat_to_yxz_euler_deg: engine Y-X-Z euler degrees -> quat.
    Multiplication order matches matrix composition (Ry*Rx*Rz), so
    qa * qb composes rotations exactly — what the old euler-ADDITION in
    _apply_glb_transform got wrong."""
    qy = glm.angleAxis(math.radians(float(e.y)), glm.vec3(0, 1, 0))
    qx = glm.angleAxis(math.radians(float(e.x)), glm.vec3(1, 0, 0))
    qz = glm.angleAxis(math.radians(float(e.z)), glm.vec3(0, 0, 1))
    return qy * qx * qz


# ------------------------------------------------- triangle picking helpers

def _triplea_ray_tris_t_vectorized(ro, rd, tris, max_t):
    """numpy Möller–Trumbore over (N,3,3) world-space triangles —
    identical math (double-sided) to _ray_triangle_t, for hi-poly meshes
    where a per-triangle Python loop would hitch the editor click."""
    try:
        with np.errstate(all="ignore"):
            v0 = tris[:, 0]
            e1 = tris[:, 1] - v0
            e2 = tris[:, 2] - v0
            p = np.cross(np.broadcast_to(rd, e2.shape), e2)
            det = np.einsum("ij,ij->i", e1, p)
            ok = np.abs(det) > 1e-12
            if not ok.any():
                return None
            inv = np.zeros_like(det)
            inv[ok] = 1.0 / det[ok]
            tv = ro - v0
            u = np.einsum("ij,ij->i", tv, p) * inv
            ok &= (u >= -1e-6) & (u <= 1.000001)
            if not ok.any():
                return None
            q = np.cross(tv, e1)
            v = np.einsum("j,ij->i", rd, q) * inv
            ok &= (v >= -1e-6) & (u + v <= 1.000001)
            if not ok.any():
                return None
            t = np.einsum("ij,ij->i", e2, q) * inv
            ok &= (t >= 1e-6) & (t <= max_t)
            if not ok.any():
                return None
            return float(np.min(t[ok]))
    except Exception:
        return None


def _triplea_ray_mesh_triangles_t(ro, rd, mesh, model, max_t):
    """Triangle-accurate ray-vs-mesh distance for scene picking.

    Returns:
        float t — nearest triangle hit distance (<= max_t)
        False   — CPU triangles exist but the ray missed ALL of them
                  (an OBB hit on empty space — the candidate must lose)
        None    — no CPU triangle data; caller falls back to the OBB t
    """
    try:
        positions, indices = _mesh_cpu_triangles(mesh)
    except Exception:
        return None
    if positions is None or indices is None:
        return None
    try:
        m = np.array([[float(model[c][r]) for c in range(4)]
                      for r in range(4)], dtype=np.float64)
        pos = np.asarray(positions, dtype=np.float64)
        world = pos @ m[:3, :3].T + m[:3, 3]
        tris = world[np.asarray(indices, dtype=np.int64).reshape(-1, 3)]
    except Exception:
        return None
    if len(tris) == 0:
        return None
    ro3 = np.array([float(ro[0]), float(ro[1]), float(ro[2])],
                   dtype=np.float64)
    rd3 = np.array([float(rd[0]), float(rd[1]), float(rd[2])],
                   dtype=np.float64)
    best = float(max_t)
    hit = False
    if len(tris) <= 40000:
        ro_t = (ro3[0], ro3[1], ro3[2])
        rd_t = (rd3[0], rd3[1], rd3[2])
        for tri in tris:
            t = _ray_triangle_t(ro_t, rd_t, tri, best)
            if t is not None:
                best = t
                hit = True
    else:
        t = _triplea_ray_tris_t_vectorized(ro3, rd3, tris, best)
        if t is not None:
            best = t
            hit = True
    return best if hit else False


# --------------------------------------------------- selection outline hull

_TRIPLEA_OUTLINE_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec3 u_center;       // local-space pivot (mesh AABB centre)
uniform float u_hull_scale;  // >1 inflates the hull about u_center
void main(){
    vec3 p = u_center + (in_position - u_center) * u_hull_scale;
    gl_Position = u_proj * u_view * u_model * vec4(p, 1.0);
}
"""

_TRIPLEA_OUTLINE_FRAG = """#version 330 core
uniform vec4 u_color;
out vec4 frag_color;
void main(){ frag_color = u_color; }
"""


# [TRIPLEA:p3_outline_maskfree_fix] Mask-free selection outline (hotfix).
#
# The previous implementation used ctx.screen.color_mask + screen depth clears,
# which desync moderngl's state tracker from real GL state on moderngl 5.x —
# the GL color write mask stayed all-False afterwards, so gizmos/grid/UI drawn
# after the outline wrote no color (the "invisible gizmos" bug).
#
# This version never touches color masks or the screen depth buffer:
#   pass A — depth-only prepass into a PRIVATE offscreen FBO, using
#            blend (ZERO, ONE) so colour writes are no-ops;
#   pass B — inflated orange hull (inverted-hull rim) into the same FBO;
#   pass C — fullscreen alpha-blended overlay compositing the rim onto the
#            screen.  Screen depth is never read or written.
_TRIPLEA_OUTLINE_OVERLAY_VERT = """#version 330 core
out vec2 v_uv;
void main(){
    vec2 v = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = v;
    gl_Position = vec4(v * 2.0 - 1.0, 0.0, 1.0);
}
"""

_TRIPLEA_OUTLINE_OVERLAY_FRAG = """#version 330 core
uniform sampler2D u_tex;
in vec2 v_uv;
out vec4 f_color;
void main(){
    vec4 c = texture(u_tex, v_uv);
    if (c.a <= 0.001)
        discard;
    f_color = c;
}
"""


def _heaven_render_selection_outline(self):
    """Unity-style orange selection outline (mask-free overlay version).

    Renders the rim into a private offscreen framebuffer and composites it
    over the screen — no color_mask, no screen-depth interaction, so no GL
    state can desync and every pass after this one (gizmos, grid, UI) draws
    normally.  Fully guarded: any failure degrades to "no outline"."""
    try:
        if getattr(self, "mode", None) != "editor":
            return
        if getattr(self, "_playing", False):
            return
        sel = getattr(self, "selected", None)
        if sel is None:
            return
        ctx = getattr(self, "ctx", None)
        if ctx is None:
            return

        # Lazily compile the hull program and the overlay program.
        prog = getattr(self, "_outline_prog", None)
        if prog is None:
            try:
                prog = ctx.program(
                    vertex_shader=_TRIPLEA_OUTLINE_VERT,
                    fragment_shader=_TRIPLEA_OUTLINE_FRAG)
            except Exception as ex:
                Log.error(f"selection outline shader failed: {ex}")
                prog = False
            self._outline_prog = prog
        if not prog:
            return
        ov_prog = getattr(self, "_outline_overlay_prog", None)
        if ov_prog is None:
            try:
                ov_prog = ctx.program(
                    vertex_shader=_TRIPLEA_OUTLINE_OVERLAY_VERT,
                    fragment_shader=_TRIPLEA_OUTLINE_OVERLAY_FRAG)
                ov_vao = ctx.vertex_array(ov_prog, [])
            except Exception as ex:
                Log.error(f"selection outline overlay shader failed: {ex}")
                ov_prog = False
                ov_vao = None
            self._outline_overlay_prog = ov_prog
            self._outline_overlay_vao = ov_vao
        ov_vao = getattr(self, "_outline_overlay_vao", None)
        if not ov_prog or ov_vao is None:
            return

        # Gather the mesh nodes of the selection subtree.
        targets = []
        for node in sel.traverse():
            mesh = getattr(node, "mesh", None)
            if mesh is None or getattr(mesh, "vbo", None) is None:
                continue
            if not getattr(node, "visible", True):
                continue
            try:
                targets.append((mesh, node.world_matrix()))
            except Exception:
                continue
        if not targets:
            return

        cam = self.camera
        view = cam.view()
        proj = cam.projection()
        prog["u_view"].write(view.to_bytes())
        prog["u_proj"].write(proj.to_bytes())
        prog["u_color"].value = (1.0, 0.55, 0.05, 1.0)  # Unity selection orange
        try:
            fov_tan = math.tan(math.radians(float(getattr(cam, "fov", 60.0))
                                           * 0.5))
        except Exception:
            fov_tan = 0.5774
        cam_pos = cam.position
        win_w = max(int(getattr(self, "width", 1280)), 1)
        win_h = max(int(getattr(self, "height", 720)), 1)

        # Lazily create (and recreate on resize) the private rim framebuffer.
        rim = getattr(self, "_outline_rim", None)
        if rim is None or rim[0] != win_w or rim[1] != win_h:
            if rim is not None:
                for res in rim[2:]:
                    try:
                        res.release()
                    except Exception:
                        pass
            try:
                rim_tex = ctx.texture((win_w, win_h), 4)
                rim_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                rim_depth = ctx.depth_renderbuffer((win_w, win_h))
                rim_fbo = ctx.framebuffer([rim_tex], rim_depth)
                rim = (win_w, win_h, rim_tex, rim_depth, rim_fbo)
            except Exception as ex:
                Log.error(f"selection outline fbo failed: {ex}")
                rim = False
            self._outline_rim = rim
        if not rim:
            return
        _w, _h, rim_tex, _rim_depth, rim_fbo = rim

        # Precompute per-mesh hull parameters (constant ~3px screen-space rim).
        items = []
        for mesh, model in targets:
            try:
                bb_min, bb_max = mesh.bounding_box
                cx = (float(bb_min.x) + float(bb_max.x)) * 0.5
                cy = (float(bb_min.y) + float(bb_max.y)) * 0.5
                cz = (float(bb_min.z) + float(bb_max.z)) * 0.5
                ex = float(bb_max.x) - float(bb_min.x)
                ey = float(bb_max.y) - float(bb_min.y)
                ez = float(bb_max.z) - float(bb_min.z)
                diag = math.sqrt(max(ex * ex + ey * ey + ez * ez, 1e-12))
                center_w = glm.vec3(model * glm.vec4(cx, cy, cz, 1.0))
                dist = max(float(glm.length(center_w - cam_pos)), 0.05)
                rim_world = 2.0 * dist * fov_tan * (3.0 / float(win_h))
                ws = max(float(glm.length(glm.vec3(model[0]))),
                         float(glm.length(glm.vec3(model[1]))),
                         float(glm.length(glm.vec3(model[2]))), 1e-6)
                half_world = max(diag * 0.5 * ws, 1e-6)
                hull_scale = 1.0 + min(rim_world / half_world, 0.5)
                flat = min(ex, ey, ez) <= 0.02 * max(ex, ey, ez, 1e-6)
                items.append((mesh, model, (cx, cy, cz), hull_scale, flat))
            except Exception:
                continue
        if not items:
            return

        def _draw(mesh, model, center, hull_scale):
            prog["u_model"].write(model.to_bytes())
            prog["u_center"].value = center
            prog["u_hull_scale"].value = float(hull_scale)
            vao = mesh.get_vao(prog)
            if mesh.indexed:
                vao.render(moderngl.TRIANGLES)
            else:
                vao.render(moderngl.TRIANGLES, vertices=mesh.vertex_count)

        # ---- passes A+B: rim into the private offscreen framebuffer ----
        rim_fbo.use()
        ctx.viewport = (0, 0, win_w, win_h)
        rim_fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        # Pass A — depth-only prepass: blend (ZERO, ONE) makes colour writes
        # no-ops while depth writes land normally.  No color_mask needed.
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.ZERO, moderngl.ONE)
        ctx.depth_func = "<"
        ctx.depth_mask = True
        for mesh, model, center, _hs, _flat in items:
            try:
                _draw(mesh, model, center, 1.0)
            except Exception:
                continue
        # Pass B — inflated orange hull; only the rim survives the depth test.
        ctx.disable(moderngl.BLEND)
        ctx.depth_mask = False
        for mesh, model, center, hull_scale, flat in items:
            try:
                if flat:
                    # Flat meshes (planes) inflate in-plane: interior equals
                    # prepass depth and fails '<', leaving only the border.
                    ctx.disable(moderngl.CULL_FACE)
                else:
                    ctx.enable(moderngl.CULL_FACE)
                    try:
                        ctx.cull = "front"
                    except Exception:
                        pass
                _draw(mesh, model, center, hull_scale)
            except Exception:
                continue
        try:
            ctx.disable(moderngl.CULL_FACE)
            try:
                ctx.cull = "back"
            except Exception:
                pass
        except Exception:
            pass
        ctx.depth_mask = True

        # ---- pass C: alpha-composite the rim over the screen ----
        ctx.screen.use()
        ctx.viewport = (0, 0, win_w, win_h)
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        try:
            rim_tex.use(location=0)
            ov_prog["u_tex"] = 0
            ov_vao.render(moderngl.TRIANGLES, vertices=3)
        except Exception:
            pass
        # Restore the engine's default post-render state: depth test on with
        # standard less-than, writes enabled, blending off.
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        ctx.depth_mask = True
    except Exception:
        # The outline is cosmetic — NEVER let it break the frame.
        try:
            self.ctx.depth_mask = True
        except Exception:
            pass


# ------------------------------------------------------- animated GIF textures

def _triplea_gif_load_entry(renderer, apath, mtime):
    """Decode every frame of an animated GIF and upload one ModernGL texture
    per frame.  Returns a cache entry dict, or None on failure.

    Frames are accumulated onto a canvas honouring the GIF disposal methods
    (0/1 keep, 2 restore-to-transparent, 3 restore-to-previous) so optimised
    difference-frame GIFs render correctly.  Rows are flipped to OpenGL's
    bottom-left origin to match get_texture's pygame path, and each frame
    gets mipmaps + 4x anisotropy exactly like static textures."""
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        Log.warn("Animated GIF textures need Pillow — pip install pillow")
        return None
    try:
        im = Image.open(apath)
        size = im.size
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        frames = []
        durations = []
        for frame in ImageSequence.Iterator(im):
            try:
                dur = int(frame.info.get("duration", 100) or 100)
            except Exception:
                dur = 100
            durations.append(max(dur, 20) / 1000.0)
            fr = frame.convert("RGBA")
            if fr.size == size:
                composed = Image.alpha_composite(canvas, fr)
            else:
                # Partial (difference-optimised) frame: paste at its offset.
                composed = canvas.copy()
                try:
                    box = frame.tile[0][1]
                    composed.paste(fr, (int(box[0]), int(box[1])), fr)
                except Exception:
                    composed.paste(fr, (0, 0), fr)
            frames.append(composed)
            disposal = getattr(frame, "disposal_method", 0)
            if disposal == 2:
                # Restore the frame's extent to transparent for the NEXT frame.
                canvas = composed.copy()
                try:
                    x0, y0, x1, y1 = frame.dispose_extent
                    clear = Image.new("RGBA",
                                      (max(1, x1 - x0), max(1, y1 - y0)),
                                      (0, 0, 0, 0))
                    canvas.paste(clear, (int(x0), int(y0)))
                except Exception:
                    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            elif disposal == 3:
                pass  # restore-to-previous: keep the pre-frame canvas
            else:
                canvas = composed
        if not frames:
            return None
        textures = []
        for fr in frames:
            data = fr.transpose(Image.FLIP_TOP_BOTTOM).tobytes()
            tex = renderer.ctx.texture(size, 4, data)
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            try:
                tex.anisotropy = 4.0
            except Exception:
                pass
            textures.append(tex)
        total = max(sum(durations), 1e-6)
        return {"mtime": mtime, "frames": textures, "durations": durations,
                "total": total, "t0": time.monotonic()}
    except Exception as ex:
        Log.warn(f"GIF decode failed for {os.path.basename(apath)}: {ex}")
        return None


def _renderer_gif_texture_for(self, path):
    """Renderer method (added by the p3_editor patch block): resolve a .gif
    path to the texture for the CURRENT loop time.

    Cached per absolute path + file mtime; time base is time.monotonic() so
    the animation advances in BOTH the editor and play mode (every material
    bind passes through get_texture each frame).  Never raises — failures
    log once and return the default white texture."""
    default = getattr(self, "_default_tex", None)
    try:
        apath = os.path.abspath(path)
    except Exception:
        return default
    cache = getattr(self, "_gif_tex_cache", None)
    if cache is None:
        cache = self._gif_tex_cache = {}
    try:
        mtime = os.path.getmtime(apath)
    except OSError:
        mtime = None
    ent = cache.get(apath)
    if ent is not None and ent.get("mtime") != mtime:
        # File changed on disk: release the stale frame textures, re-decode.
        for t in ent.get("frames", []) or []:
            try:
                t.release()
            except Exception:
                pass
        cache.pop(apath, None)
        ent = None
    if ent is None:
        ent = _triplea_gif_load_entry(self, apath, mtime)
        if ent is None:
            cache[apath] = {"mtime": mtime, "failed": True}
            return default
        cache[apath] = ent
    if ent.get("failed"):
        return default
    frames = ent["frames"]
    if len(frames) <= 1:
        return frames[0]
    t = (time.monotonic() - ent["t0"]) % ent["total"]
    acc = 0.0
    for i, d in enumerate(ent["durations"]):
        acc += d
        if t < acc:
            return frames[i]
    return frames[-1]


# ------------------------------------------------------- inspector GIF rows

def _heaven_plane_gif_dialog(self, node):
    """GIF… button (planes only): pick an animated GIF and bind it to the
    plane material's albedo slot.  The ("path", p) descriptor form is the
    one Material.to_dict / ProjectSerializer already round-trip, so the
    assignment survives project save/load."""
    try:
        path = self._native_dialog(
            "open", "Choose Animated GIF",
            filetypes=[("Animated GIF", "*.gif"), ("All files", "*.*")])
        if not path:
            return
        if node.material is None:
            node.material = Material(name=f"{node.name}_mat")
        node.material.base_color_tex = ("path", os.path.abspath(path))
        Log.info(f"{node.name}: GIF texture set to "
                 f"{os.path.basename(path)} (loops on the plane)")
    except Exception as ex:
        Log.error(f"GIF picker failed: {ex}")


def _heaven_clear_plane_gif(self, node):
    """Clear GIF button: restore the plane material's empty albedo slot."""
    try:
        if node.material is not None:
            node.material.base_color_tex = None
            Log.info(f"{node.name}: GIF texture cleared")
    except Exception as ex:
        Log.error(f"Clear GIF failed: {ex}")


# ------------------------------------------------------------- registration
HeavenEngine._render_selection_outline = _heaven_render_selection_outline
HeavenEngine._plane_gif_dialog = _heaven_plane_gif_dialog
HeavenEngine._clear_plane_gif = _heaven_clear_plane_gif
Renderer._gif_texture_for = _renderer_gif_texture_for



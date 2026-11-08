# @heaven3d-part patches/quality_fixes_v1.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# FINALPATCH — QUALITY FIXES V1
# [FINALPATCH:quality_fixes_v1]
#   * Front-to-back correct ViewGizmo labels, hover and clicking
#   * Pixel-exact mask/edge selection outline (no distorted inverted hull)
#   * Player-capsule contact pickup collection (no mouse click required)
#   * Safe resource cleanup for the new outline framebuffer
# This block intentionally monkey-patches only the defective methods.  It is
# inserted before main(), so it is active in both editor and runtime builds.
# #############################################################################

# ---------------------------------------------------------- ViewGizmo fixes

def _finalpatch_viewgizmo_projected_parts(self, camera, win_w, win_h):
    """Project every positive ball / negative ring with depth and radius."""
    parts = []
    try:
        gx, gy, s = self.region_px(win_w, win_h)
        for label, pt, kind in self._part_points():
            ndc = self._project_ndc(camera, pt)
            px = gx + (float(ndc.x) * 0.5 + 0.5) * s
            py = gy + (float(ndc.y) * 0.5 + 0.5) * s
            visual_r = self._BALL_R if kind == "ball" else self._RING_MAJOR
            radius = visual_r / self._HALF * (s * 0.5) + 5.0
            parts.append({"label": label, "kind": kind, "x": px, "y": py,
                          "z": float(ndc.z), "radius": float(radius)})
    except Exception:
        return []
    return parts


def _finalpatch_viewgizmo_hit_test(self, mouse_pos, win_w, win_h, camera):
    """Return the FRONTMOST axis part under the cursor.

    The old implementation chose the nearest projected centre and never used
    depth.  When two axes overlapped, the hidden rear axis could receive hover
    and clicks.  NDC Z is lower for nearer geometry in this orthographic pass,
    so depth is the primary ordering and screen distance is only a tie-break.
    """
    if not self.point_in_region(mouse_pos, win_w, win_h):
        return None
    mx, my = mouse_pos
    my = win_h - my
    candidates = []
    for part in _finalpatch_viewgizmo_projected_parts(
            self, camera, win_w, win_h):
        dist = math.hypot(mx - part["x"], my - part["y"])
        if dist <= part["radius"]:
            candidates.append((part["z"], dist, part["label"]))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _finalpatch_viewgizmo_emit_labels(self, ui, win_w, win_h, camera):
    """Queue X/Y/Z labels only when their own ball is actually visible.

    Geometry was already depth-tested in the offscreen navball pass, but the
    letters were later emitted through the always-on-top UI pass.  A hidden
    rear ball could therefore stamp its letter onto a different front ball.
    This uses the same projection and NDC depth as the geometry pass and
    suppresses a label when a nearer ball/ring covers its projected centre.
    """
    parts = _finalpatch_viewgizmo_projected_parts(
        self, camera, win_w, win_h)
    if not parts:
        return
    lh = 15.0
    for ball in parts:
        if ball["kind"] != "ball":
            continue
        visible = True
        for other in parts:
            if other is ball:
                continue
            # A conservative overlap test: only another substantial navball
            # part can hide a letter; distant edge touches never flicker it.
            cover_radius = max(2.0,
                               (ball["radius"] + other["radius"]) * 0.58)
            distance = math.hypot(ball["x"] - other["x"],
                                  ball["y"] - other["y"])
            if other["z"] < ball["z"] - 0.002 and distance < cover_radius:
                visible = False
                break
        if not visible:
            continue
        letter = ball["label"].upper()
        try:
            tw, th = ui.font.text_size(letter)
        except Exception:
            continue
        w_px = lh * (tw / max(1, th))
        ui.text(UIRect((ball["x"] - w_px * 0.5) / win_w,
                       (ball["y"] - lh * 0.5) / win_h,
                       (w_px + 2.0) / win_w,
                       lh / win_h),
                letter, (1.0, 1.0, 1.0, 1.0))


ViewGizmo.hit_test = _finalpatch_viewgizmo_hit_test
ViewGizmo.emit_labels = _finalpatch_viewgizmo_emit_labels


# --------------------------------------------- pixel-exact selection outline

_FINALPATCH_OUTLINE_MASK_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
void main(){
    gl_Position = u_proj * u_view * u_model * vec4(in_position, 1.0);
}
"""

_FINALPATCH_OUTLINE_MASK_FRAG = """#version 330 core
out vec4 frag_color;
void main(){ frag_color = vec4(1.0); }
"""

_FINALPATCH_OUTLINE_EDGE_VERT = """#version 330 core
out vec2 v_uv;
void main(){
    vec2 v = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = v;
    gl_Position = vec4(v * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FINALPATCH_OUTLINE_EDGE_FRAG = """#version 330 core
uniform sampler2D u_mask;
uniform vec2 u_texel;
uniform float u_radius;
in vec2 v_uv;
out vec4 frag_color;
void main(){
    // Outside-only edge: never paint the object's interior and never move
    // geometry.  The ring follows the rendered mask pixel-for-pixel.
    if (texture(u_mask, v_uv).a > 0.01)
        discard;
    const int SAMPLES = 16;
    float found = 0.0;
    for (int i = 0; i < SAMPLES; ++i){
        float a = 6.28318530718 * (float(i) / float(SAMPLES));
        vec2 uv = v_uv + vec2(cos(a), sin(a)) * u_texel * u_radius;
        found = max(found, texture(u_mask, uv).a);
    }
    if (found <= 0.01)
        discard;
    frag_color = vec4(1.0, 0.55, 0.05, 1.0);
}
"""


def _finalpatch_outline_release_resources(self):
    holder = getattr(self, "_finalpatch_outline_mask", None)
    if holder in (None, False):
        return
    try:
        for resource in holder[2:]:
            try:
                resource.release()
            except Exception:
                pass
    finally:
        self._finalpatch_outline_mask = None


def _finalpatch_render_selection_outline(self):
    """Pixel-exact Unity-style selection outline.

    The previous inverted-hull method scaled each mesh around its local AABB
    centre.  Long, rotated, non-uniformly scaled or multi-part imports could
    produce a visibly offset green/orange ghost mesh.  This replacement draws
    the selected subtree's real triangles into a private mask FBO and performs
    edge detection in screen space.  No vertex is moved, so the outline cannot
    drift from the rendered model.
    """
    try:
        if getattr(self, "mode", None) != "editor":
            return
        if getattr(self, "_playing", False):
            return
        if getattr(self, "_finalpatch_outline_failed", False):
            return
        selected = getattr(self, "selected", None)
        ctx = getattr(self, "ctx", None)
        if selected is None or ctx is None:
            return

        mask_prog = getattr(self, "_finalpatch_outline_mask_prog", None)
        edge_prog = getattr(self, "_finalpatch_outline_edge_prog", None)
        edge_vao = getattr(self, "_finalpatch_outline_edge_vao", None)
        if mask_prog is None or edge_prog is None or edge_vao is None:
            try:
                mask_prog = ctx.program(
                    vertex_shader=_FINALPATCH_OUTLINE_MASK_VERT,
                    fragment_shader=_FINALPATCH_OUTLINE_MASK_FRAG)
                edge_prog = ctx.program(
                    vertex_shader=_FINALPATCH_OUTLINE_EDGE_VERT,
                    fragment_shader=_FINALPATCH_OUTLINE_EDGE_FRAG)
                edge_vao = ctx.vertex_array(edge_prog, [])
            except Exception as ex:
                self._finalpatch_outline_failed = True
                Log.error(f"selection outline shader failed: {ex}")
                return
            self._finalpatch_outline_mask_prog = mask_prog
            self._finalpatch_outline_edge_prog = edge_prog
            self._finalpatch_outline_edge_vao = edge_vao

        targets = []
        for node in selected.traverse():
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

        win_w = max(int(getattr(self, "width", 1280)), 1)
        win_h = max(int(getattr(self, "height", 720)), 1)
        holder = getattr(self, "_finalpatch_outline_mask", None)
        if holder is None or holder[0] != win_w or holder[1] != win_h:
            _finalpatch_outline_release_resources(self)
            try:
                mask_tex = ctx.texture((win_w, win_h), 4)
                mask_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                mask_depth = ctx.depth_renderbuffer((win_w, win_h))
                mask_fbo = ctx.framebuffer([mask_tex], mask_depth)
                holder = (win_w, win_h, mask_tex, mask_depth, mask_fbo)
                self._finalpatch_outline_mask = holder
            except Exception as ex:
                self._finalpatch_outline_failed = True
                Log.error(f"selection outline framebuffer failed: {ex}")
                return
        _w, _h, mask_tex, _mask_depth, mask_fbo = holder

        camera = self.camera
        mask_prog["u_view"].write(camera.view().to_bytes())
        mask_prog["u_proj"].write(camera.projection().to_bytes())

        # Pass 1: exact selected-geometry mask.
        mask_fbo.use()
        ctx.viewport = (0, 0, win_w, win_h)
        mask_fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        ctx.depth_mask = True
        ctx.disable(moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        for mesh, model in targets:
            try:
                mask_prog["u_model"].write(model.to_bytes())
                vao = mesh.get_vao(mask_prog)
                if mesh.indexed:
                    vao.render(moderngl.TRIANGLES)
                else:
                    vao.render(moderngl.TRIANGLES,
                               vertices=mesh.vertex_count)
            except Exception:
                continue

        # Pass 2: outside-only orange edge, composited over the screen.
        ctx.screen.use()
        ctx.viewport = (0, 0, win_w, win_h)
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA,
                          moderngl.ONE_MINUS_SRC_ALPHA)
        edge_prog["u_tex"] = 0
        edge_prog["u_texel"] = (1.0 / win_w, 1.0 / win_h)
        edge_prog["u_radius"] = 2.0
        mask_tex.use(location=0)
        edge_vao.render(moderngl.TRIANGLES, vertices=3)

        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        ctx.depth_mask = True
    except Exception:
        # Cosmetic overlay: restore safe GL state and never break a frame.
        try:
            self.ctx.screen.use()
            self.ctx.disable(moderngl.BLEND)
            self.ctx.enable(moderngl.DEPTH_TEST)
            self.ctx.depth_func = "<"
            self.ctx.depth_mask = True
        except Exception:
            pass


HeavenEngine._render_selection_outline = _finalpatch_render_selection_outline

_FINALPATCH_HEAVEN_RELEASE = HeavenEngine.release


def _finalpatch_heaven_release(self):
    _finalpatch_outline_release_resources(self)
    _FINALPATCH_HEAVEN_RELEASE(self)


HeavenEngine.release = _finalpatch_heaven_release


# --------------------------------------------------- player-contact pickups

_FINALPATCH_PICKUP_PREVIOUS_ON_TICK = Pickup.on_tick


def _finalpatch_pickup_collect(self, player):
    """Apply the pickup effect once; shared by physics and contact checks."""
    if self._collected or player is None:
        return
    self._collected = True
    kind = str(self.p("kind"))
    amount = self.p("amount")
    if kind == "health":
        self.engine.message_node(player, "heal", {"amount": amount})
    elif kind == "ammo":
        self.engine.message_subtree(player, "add_ammo",
                                    {"amount": amount})
    else:
        self.engine.send_message(str(self.p("event_name")),
                                 {"node": self.node, "other": player})
    self.engine.audio.play_at(str(self.p("sound")),
                              self.node.world_position(), 0.7)
    self.node.visible = False
    self.engine.physics.remove_node(self.node)
    respawn_time = float(self.p("respawn_time"))
    if respawn_time > 0.0:
        self.engine.schedule(respawn_time, self._respawn)


def _finalpatch_pickup_touches_player(self, player_bp) -> bool:
    """Capsule-vs-pickup contact test using world-space bounds.

    The old fallback implicitly treated both objects as points, so the player
    capsule could visibly touch a pickup while their centres were still too far
    apart.  Measure from the player's vertical capsule axis to the pickup's
    current physics AABB and expand by the capsule radius plus a small contact
    skin.  This triggers on touch, not on a mouse click.
    """
    try:
        if player_bp is None or getattr(player_bp, "_dead", False):
            return False
        player_node = player_bp.node
        if player_node is None or not player_node.visible:
            return False
        controller = getattr(player_bp, "cc", None)
        player_center = (controller.position if controller is not None
                         else player_node.world_position())
        player_radius = float(getattr(controller, "radius", 0.35))
        player_height = float(getattr(controller, "height", 1.8))
        pickup_center = self.node.world_position()

        lo = hi = None
        for entry in getattr(self.engine.physics, "entries", ()):
            if entry.node is not self.node:
                continue
            try:
                lo, hi = _shape_aabb(entry.shape())
            except Exception:
                lo = hi = None
            break

        # Closest point on the player's capsule segment to the pickup centre.
        bottom = float(player_center.y) - player_height * 0.5
        top = float(player_center.y) + player_height * 0.5
        axis_y = _clamp(float(pickup_center.y), bottom, top)
        axis_point = glm.vec3(float(player_center.x), axis_y,
                              float(player_center.z))

        if lo is not None and hi is not None:
            closest = glm.vec3(
                _clamp(float(axis_point.x), float(lo.x), float(hi.x)),
                _clamp(float(axis_point.y), float(lo.y), float(hi.y)),
                _clamp(float(axis_point.z), float(lo.z), float(hi.z)))
            gap = float(glm.length(axis_point - closest))
        else:
            radius = max(0.05, float(self.p("trigger_radius", 0.55)))
            gap = max(0.0, float(glm.length(axis_point - pickup_center))
                      - radius)
        return gap <= player_radius + 0.08
    except Exception:
        return False


def _finalpatch_pickup_on_tick(self, dt: float):
    if getattr(self, "_collected", False):
        return
    _FINALPATCH_PICKUP_PREVIOUS_ON_TICK(self, dt)
    if getattr(self, "_collected", False):
        return
    player_bp = self._player_bp()
    if player_bp is not None and _finalpatch_pickup_touches_player(
            self, player_bp):
        _finalpatch_pickup_collect(self, player_bp.node)


def _finalpatch_pickup_on_trigger_enter(self, other):
    if getattr(self, "_collected", False):
        return
    player = self._player_root(other)
    if player is not None:
        _finalpatch_pickup_collect(self, player)


Pickup.on_tick = _finalpatch_pickup_on_tick
Pickup.on_trigger_enter = _finalpatch_pickup_on_trigger_enter


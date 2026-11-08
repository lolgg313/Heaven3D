# @heaven3d-part patches/single_object_imports_icons.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# FINALPATCH — SINGLE-OBJECT IMPORTS, PHYSICS ROTATION, UNREAL-STYLE ICONS V2
# [FINALPATCH:single_object_icons_physics_v2]
# This block is intentionally self-contained and editor-safe:
#   * imported GLB/glTF sub-nodes stay functional but are hidden from Hierarchy
#   * imported-body angular composition follows the main node's rotation
#   * toolbar and 3D editor icons are generated procedurally at runtime
#   * no image/font/asset files are required
# #############################################################################


def _finalpatch_is_imported_glb_node(node) -> bool:
    """True only for GLB/glTF roots and their auto-generated import parts."""
    try:
        marker = getattr(node, "_heaven3d_imported_part", None)
        if isinstance(marker, str):
            return marker.lower() in (".glb", ".gltf", ".blend")
        path = str(getattr(node, "source_path", "") or "")
        return os.path.splitext(path)[1].lower() in (".glb", ".gltf", ".blend")
    except Exception:
        return False


def _finalpatch_integrate_body_orientation(body, entry, h: float):
    """Integrate angular velocity with the imported model's rotation basis.

    Procedural nodes keep the engine's original world-space composition.  GLB
    geometry is world-baked by the importer, so a right-side local increment is
    the convention that keeps the physics body and the visible main object
    turning in the same direction.  The branch is deliberately limited to
    imported models; every other physics body keeps its original behavior.
    """
    w = body.angular_velocity
    wq = glm.quat(0.0, w.x * 0.5 * h, w.y * 0.5 * h,
                  w.z * 0.5 * h)
    node = getattr(entry, "node", entry)
    if _finalpatch_is_imported_glb_node(node):
        body.orientation = glm.normalize(
            body.orientation + body.orientation * wq)
    else:
        body.orientation = glm.normalize(
            body.orientation + wq * body.orientation)


# ---------------------------------------------------------------------------
# Embedded Unreal-style icon drawing (vector strokes, no external assets)
# ---------------------------------------------------------------------------

_FP_MENU_ICONS = {
    "Play": "play", "Stop": "stop", "Save": "save", "Load": "load",
    "Export": "export", "Cube": "cube", "Plane": "plane",
    "Sphere": "sphere", "Import": "import", "Library": "library",
}

_FP_BILLBOARD_KINDS = (
    "point", "spot", "sun", "player", "enemy", "ammo", "health", "trigger",
)


def _fp_icon_surface(kind: str, size: int = 64):
    """Draw a clean flat editor icon entirely in code and return a Surface."""
    scale = 3
    S = int(size) * scale
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    c = (232, 236, 241, 255)
    dim = (166, 176, 188, 255)
    blue = (92, 178, 255, 255)
    green = (85, 220, 145, 255)
    red = (255, 92, 82, 255)
    yellow = (255, 202, 74, 255)
    orange = (255, 150, 55, 255)
    purple = (190, 112, 255, 255)
    w = max(3, S // 24)
    C = S // 2

    def line(points, color=c, width=w, closed=False):
        pygame.draw.lines(surf, color, closed, points, width)

    def circle(center, radius, color=c, width=w):
        pygame.draw.circle(surf, color, center, int(radius), width)

    def rect(r, color=c, width=w, radius=0):
        pygame.draw.rect(surf, color, r, width, border_radius=int(radius))

    if kind == "play":
        circle((C, C), S * 0.36, green)
        pygame.draw.polygon(surf, green, [(S*.40, S*.30), (S*.40, S*.70), (S*.72, S*.50)])
    elif kind == "stop":
        circle((C, C), S * 0.36, red)
        rect((S*.35, S*.35, S*.30, S*.30), red, 0, S*.04)
    elif kind == "save":
        rect((S*.24, S*.22, S*.52, S*.58), c, w, S*.05)
        rect((S*.35, S*.27, S*.24, S*.17), dim, max(2, w//2), S*.02)
        rect((S*.34, S*.55, S*.32, S*.20), blue, max(2, w//2), S*.02)
    elif kind == "load":
        line([(S*.20,S*.40),(S*.34,S*.40),(S*.40,S*.32),(S*.76,S*.32),(S*.76,S*.70),(S*.20,S*.70)], c, w, True)
        line([(S*.50,S*.42),(S*.50,S*.60)], yellow)
        line([(S*.40,S*.53),(S*.50,S*.63),(S*.60,S*.53)], yellow)
    elif kind == "export":
        line([(S*.28,S*.56),(S*.28,S*.76),(S*.72,S*.76),(S*.72,S*.56)], dim)
        line([(S*.50,S*.68),(S*.50,S*.25)], blue)
        line([(S*.35,S*.40),(S*.50,S*.25),(S*.65,S*.40)], blue)
    elif kind == "import":
        line([(S*.28,S*.56),(S*.28,S*.76),(S*.72,S*.76),(S*.72,S*.56)], dim)
        line([(S*.50,S*.25),(S*.50,S*.60)], green)
        line([(S*.35,S*.47),(S*.50,S*.62),(S*.65,S*.47)], green)
    elif kind == "cube":
        top = (S*.50, S*.22); left = (S*.25, S*.37); right = (S*.75, S*.37)
        mid = (S*.50, S*.52); bl = (S*.25, S*.67); br = (S*.75, S*.67); bot = (S*.50, S*.82)
        line([top, left, mid, right, top], blue, w, True)
        line([left, bl, bot, br, right], blue)
        line([mid, bot], blue)
    elif kind == "plane":
        line([(S*.20,S*.62),(S*.50,S*.44),(S*.80,S*.62),(S*.50,S*.80)], green, w, True)
        for x in (.32, .44, .56, .68):
            line([(S*x,S*.55),(S*x,S*.69)], dim, max(2,w//2))
        line([(S*.35,S*.53),(S*.65,S*.71)], dim, max(2,w//2))
        line([(S*.65,S*.53),(S*.35,S*.71)], dim, max(2,w//2))
    elif kind == "sphere":
        circle((C,C), S*.32, purple)
        pygame.draw.ellipse(surf, dim, (S*.20,S*.39,S*.60,S*.24), max(2,w//2))
        pygame.draw.arc(surf, dim, (S*.34,S*.20,S*.32,S*.62), -1.2, 1.2, max(2,w//2))
    elif kind == "library":
        for i, y in enumerate((.30, .48, .66)):
            col = blue if i == 0 else (dim if i == 1 else green)
            line([(S*.20,S*y),(S*.50,S*(y-.12)),(S*.80,S*y),(S*.50,S*(y+.12))], col, w, True)
    elif kind == "point":
        circle((C,S*.42), S*.20, yellow)
        rect((S*.42,S*.58,S*.16,S*.14), yellow, max(2,w//2), S*.02)
        for a in range(8):
            ang = math.tau * a / 8.0
            x1 = C + math.cos(ang)*S*.31; y1 = S*.42 + math.sin(ang)*S*.31
            x2 = C + math.cos(ang)*S*.40; y2 = S*.42 + math.sin(ang)*S*.40
            line([(x1,y1),(x2,y2)], yellow, max(2,w//2))
    elif kind == "spot":
        line([(S*.28,S*.30),(S*.55,S*.30),(S*.76,S*.46),(S*.76,S*.58),(S*.55,S*.74),(S*.28,S*.74)], yellow, w, True)
        line([(S*.28,S*.30),(S*.28,S*.74)], yellow)
        for y in (.38,.52,.66):
            line([(S*.78,S*y),(S*.90,S*y)], yellow, max(2,w//2))
    elif kind == "sun":
        circle((C,C), S*.18, yellow)
        for a in range(12):
            ang = math.tau * a / 12.0
            line([(C+math.cos(ang)*S*.27, C+math.sin(ang)*S*.27),
                  (C+math.cos(ang)*S*.40, C+math.sin(ang)*S*.40)], yellow, max(2,w//2))
    elif kind == "player":
        circle((C,S*.30), S*.12, blue)
        rect((S*.39,S*.43,S*.22,S*.34), blue, w, S*.11)
        line([(S*.50,S*.77),(S*.50,S*.88)], blue)
        line([(S*.38,S*.57),(S*.24,S*.68)], blue)
        line([(S*.62,S*.57),(S*.76,S*.68)], blue)
    elif kind == "enemy":
        circle((C,C), S*.30, red)
        line([(S*.32,S*.29),(S*.22,S*.15)], red)
        line([(S*.68,S*.29),(S*.78,S*.15)], red)
        circle((S*.41,S*.47), S*.035, red, 0)
        circle((S*.59,S*.47), S*.035, red, 0)
        line([(S*.39,S*.66),(S*.46,S*.61),(S*.54,S*.66),(S*.61,S*.61)], red, max(2,w//2))
    elif kind == "ammo":
        for x in (.34,.50,.66):
            rect((S*(x-.055),S*.34,S*.11,S*.38), orange, max(2,w//2), S*.05)
            pygame.draw.polygon(surf, orange, [(S*(x-.055),S*.34),(S*x,S*.20),(S*(x+.055),S*.34)])
    elif kind == "health":
        circle((C,C), S*.35, green)
        rect((S*.45,S*.28,S*.10,S*.44), green, 0, S*.02)
        rect((S*.28,S*.45,S*.44,S*.10), green, 0, S*.02)
    elif kind == "trigger":
        rect((S*.23,S*.23,S*.54,S*.54), purple, w, S*.08)
        pygame.draw.polygon(surf, yellow, [(S*.55,S*.29),(S*.39,S*.55),(S*.51,S*.55),(S*.45,S*.73),(S*.64,S*.47),(S*.52,S*.47)])
    else:
        circle((C,C), S*.32, c)

    if size != S:
        surf = pygame.transform.smoothscale(surf, (int(size), int(size)))
    return surf


def _finalpatch_menu_icon(self, kind: str):
    """Lazy-upload one procedural toolbar icon texture."""
    cache = getattr(self, "_fp_menu_icon_cache", None)
    if cache is None:
        cache = {}
        self._fp_menu_icon_cache = cache
    if kind in cache:
        return cache[kind]
    try:
        surf = _fp_icon_surface(kind, 64)
        data = pygame.image.tostring(surf, "RGBA", True)
        tex = self.ctx.texture(surf.get_size(), 4, data)
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        cache[kind] = tex
        return tex
    except Exception as ex:
        if not getattr(self, "_fp_menu_icons_failed", False):
            self._fp_menu_icons_failed = True
            Log.warn(f"Embedded toolbar icons disabled: {ex}")
        cache[kind] = None
        return None


def _finalpatch_ui_render(self):
    """UIRenderer.render with embedded Unreal-style toolbar icons.

    Every non-toolbar button follows the original renderer byte-for-byte in
    behavior; only top-navbar labels in _FP_MENU_ICONS gain an icon and shift
    their text to make room for it.
    """
    if self._prog is None:
        return
    self.ctx.disable(moderngl.DEPTH_TEST)
    self.ctx.enable(moderngl.BLEND)
    self.ctx.blend_func = (moderngl.SRC_ALPHA,
                           moderngl.ONE_MINUS_SRC_ALPHA)
    for rect, color in self._panel_rects:
        self._draw_rect(rect.to_pixels(self.w, self.h), color)

    for rect, text, color, vp in self._text_rects:
        if vp is not None:
            self._enable_scissor(vp)
        tex = self.font.render_text(
            text, (int(color[0]*255), int(color[1]*255), int(color[2]*255)))
        if tex is not None:
            tw, th = tex.size
            self._draw_rect(rect.to_pixels(self.w, self.h), color,
                            tex=tex, tex_aspect=tw / max(1, th),
                            anchor_x=0.0, anchor_y=0.5)
        if vp is not None:
            self._disable_scissor()

    mx, my = self._mouse
    for (rect, label, callback, color, vp, align,
         text_color, pad_x, pad_y) in self._button_rects:
        if vp is not None:
            self._enable_scissor(vp)
        hovered = (rect.contains(mx, my)
                   and (vp is None or vp.contains(mx, my)))
        bg = _lighten(color, 0.13) if hovered else color
        rx, ry, rw, rh = rect.to_pixels(self.w, self.h)
        self._draw_rect((rx, ry, rw, rh), bg, radius=UI_RADIUS)
        tcol = THEME["text_bright"] if hovered \
            else (text_color or THEME["text"])

        icon_key = None
        if rect.y >= 0.935:
            icon_key = _FP_MENU_ICONS.get(label)
        icon_tex = (_finalpatch_menu_icon(self, icon_key)
                    if icon_key is not None else None)
        icon_px = 0.0
        if icon_tex is not None:
            icon_px = min(22.0, max(14.0, rh - 10.0))
            ix = rx + 6.0
            iy = ry + (rh - icon_px) * 0.5
            self._draw_rect((ix, iy, icon_px, icon_px), (1, 1, 1, 1),
                            tex=icon_tex, tex_aspect=1.0,
                            anchor_x=0.0, anchor_y=0.5)

        tex = self.font.render_text(
            label, (int(tcol[0] * 255), int(tcol[1] * 255),
                    int(tcol[2] * 255)))
        if tex is not None:
            tw, th = tex.size
            tx = rx + pad_x + (icon_px + 5.0 if icon_tex is not None else 0.0)
            ty = ry + pad_y
            tw_avail = max(1.0, rw - 2.0 * pad_x -
                           (icon_px + 5.0 if icon_tex is not None else 0.0))
            th_avail = max(1.0, rh - 2.0 * pad_y)
            self._draw_rect((tx, ty, tw_avail, th_avail), tcol,
                            tex=tex, tex_aspect=tw / max(1, th),
                            anchor_x=0.0 if align == "left" else 0.5,
                            anchor_y=0.5)
        if vp is not None:
            self._disable_scissor()

    for rect, color, radius, hoverable in self._scrollbar_rects:
        c = _lighten(color, 0.15) \
            if (hoverable and rect.contains(mx, my)) else color
        self._draw_rect(rect.to_pixels(self.w, self.h), c, radius=radius)

    self.ctx.disable(moderngl.BLEND)
    self.ctx.enable(moderngl.DEPTH_TEST)


_FP_PREV_UI_RENDER = UIRenderer.render
UIRenderer.render = _finalpatch_ui_render
_FP_PREV_UI_RELEASE = UIRenderer.release


def _finalpatch_ui_release(self):
    cache = getattr(self, "_fp_menu_icon_cache", None)
    if cache:
        for tex in cache.values():
            try:
                if tex is not None:
                    tex.release()
            except Exception:
                pass
        cache.clear()
    _FP_PREV_UI_RELEASE(self)


UIRenderer.release = _finalpatch_ui_release


# ---------------------------------------------------------------------------
# Camera-facing 3D editor billboards
# ---------------------------------------------------------------------------

_FP_BILLBOARD_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec2 in_uv;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec2 v_uv;
void main(){
    v_uv = in_uv;
    gl_Position = u_proj * u_view * vec4(in_position, 1.0);
}
"""

_FP_BILLBOARD_FRAG = """#version 330 core
uniform sampler2D u_tex;
in vec2 v_uv;
out vec4 frag_color;
void main(){
    vec4 c = texture(u_tex, v_uv);
    if (c.a < 0.025) discard;
    frag_color = c;
}
"""


class _FinalPatchBillboards:
    """Unreal-style actor icons rendered as camera-facing textured quads."""

    _CELL = 64
    _MAX_VERTICES = 4092

    def __init__(self, ctx):
        self.ctx = ctx
        self._prog = None
        self._vbo = None
        self._vao = None
        self._atlas = None
        self._failed = False

    def _ensure(self) -> bool:
        if self._failed:
            return False
        if self._vao is not None and self._atlas is not None:
            return True
        try:
            self._prog = self.ctx.program(
                vertex_shader=_FP_BILLBOARD_VERT,
                fragment_shader=_FP_BILLBOARD_FRAG)
            self._vbo = self.ctx.buffer(
                reserve=self._MAX_VERTICES * 5 * 4, dynamic=True)
            self._vao = self.ctx.vertex_array(
                self._prog,
                [(self._vbo, "3f 2f", "in_position", "in_uv")])
            surf = pygame.Surface(
                (self._CELL * len(_FP_BILLBOARD_KINDS), self._CELL),
                pygame.SRCALPHA)
            for i, kind in enumerate(_FP_BILLBOARD_KINDS):
                surf.blit(_fp_icon_surface(kind, self._CELL),
                          (i * self._CELL, 0))
            data = pygame.image.tostring(surf, "RGBA", True)
            self._atlas = self.ctx.texture(surf.get_size(), 4, data)
            self._atlas.filter = (moderngl.LINEAR, moderngl.LINEAR)
            return True
        except Exception as ex:
            self._failed = True
            Log.warn(f"Embedded 3D editor billboards disabled: {ex}")
            return False

    @staticmethod
    def _node_icon(node):
        if getattr(node, "is_point_light", False):
            return "point", 0.0
        if getattr(node, "is_spot_light", False):
            return "spot", 0.0
        if getattr(node, "is_sun", False):
            return "sun", 0.0
        kind = getattr(node, "spawn_kind", None)
        if kind == "player":
            return "player", 1.18
        if kind == "enemy":
            return "enemy", 0.48
        if kind == "bullet_pickup":
            return "ammo", 0.38
        if kind == "health_pickup":
            return "health", 0.38
        if kind == "trigger":
            return "trigger", 0.72
        bp_names = {type(bp).__name__ for bp in getattr(node, "blueprints", ())}
        if "TriggerZone" in bp_names:
            return "trigger", 0.72
        if "Pickup" in bp_names:
            return "ammo", 0.38
        if "EnemyAI" in bp_names:
            return "enemy", 0.48
        if "FPSController" in bp_names:
            return "player", 1.18
        return None, 0.0

    def render(self, camera: Camera, scene: Scene):
        if not self._ensure():
            return
        try:
            items = []
            for node in scene.root.traverse():
                if not getattr(node, "visible", True):
                    continue
                kind, yoff = self._node_icon(node)
                if kind is None:
                    continue
                center = node.world_position() + glm.vec3(0.0, yoff, 0.0)
                dist = glm.length(center - camera.position)
                items.append((float(dist), node, kind, center))
            if not items:
                return
            items.sort(key=lambda item: item[0], reverse=True)

            fwd = camera.forward()
            right = camera.right()
            up = _safe_normalize(glm.cross(right, fwd), glm.vec3(0, 1, 0))
            verts = []
            for dist, _node, kind, center in items:
                # Perspective-scaled but clamped: readable near and far, like
                # Unreal's scene-view actor sprites.
                size = _clamp(dist * 0.052, 0.48, 1.35)
                h = size * 0.5
                idx = _FP_BILLBOARD_KINDS.index(kind)
                u0 = idx / float(len(_FP_BILLBOARD_KINDS))
                u1 = (idx + 1) / float(len(_FP_BILLBOARD_KINDS))
                bl = center - right * h - up * h
                br = center + right * h - up * h
                tr = center + right * h + up * h
                tl = center - right * h + up * h
                verts.extend((bl.x,bl.y,bl.z,u0,0.0, br.x,br.y,br.z,u1,0.0,
                              tr.x,tr.y,tr.z,u1,1.0,
                              bl.x,bl.y,bl.z,u0,0.0, tr.x,tr.y,tr.z,u1,1.0,
                              tl.x,tl.y,tl.z,u0,1.0))

            self._prog["u_view"].write(camera.view().to_bytes())
            self._prog["u_proj"].write(camera.projection().to_bytes())
            self._prog["u_tex"] = 0
            self._atlas.use(location=0)
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.SRC_ALPHA,
                                   moderngl.ONE_MINUS_SRC_ALPHA)
            self.ctx.depth_mask = False
            for start in range(0, len(verts), self._MAX_VERTICES * 5):
                chunk = verts[start:start + self._MAX_VERTICES * 5]
                if not chunk:
                    continue
                self._vbo.write(np.asarray(chunk, dtype=np.float32).tobytes())
                self._vao.render(moderngl.TRIANGLES,
                                 vertices=len(chunk) // 5)
            self.ctx.depth_mask = True
            self.ctx.disable(moderngl.BLEND)
            self.ctx.enable(moderngl.DEPTH_TEST)
        except Exception as ex:
            self._failed = True
            try:
                self.ctx.depth_mask = True
                self.ctx.disable(moderngl.BLEND)
                self.ctx.enable(moderngl.DEPTH_TEST)
            except Exception:
                pass
            Log.warn(f"Embedded 3D editor billboards disabled: {ex}")

    def release(self):
        for obj in (self._vao, self._vbo, self._prog, self._atlas):
            try:
                if obj is not None:
                    obj.release()
            except Exception:
                pass
        self._vao = self._vbo = self._prog = self._atlas = None


_FP_PREV_SPAWN_GIZMOS_RENDER = SpawnGizmos.render
_FP_PREV_SPAWN_GIZMOS_RELEASE = SpawnGizmos.release


def _finalpatch_spawn_gizmos_render(self, camera: Camera, scene: Scene):
    _FP_PREV_SPAWN_GIZMOS_RENDER(self, camera, scene)
    renderer = getattr(self, "_fp_billboard_renderer", None)
    if renderer is None:
        renderer = _FinalPatchBillboards(self.ctx)
        self._fp_billboard_renderer = renderer
    renderer.render(camera, scene)


def _finalpatch_spawn_gizmos_release(self):
    renderer = getattr(self, "_fp_billboard_renderer", None)
    if renderer is not None:
        renderer.release()
        self._fp_billboard_renderer = None
    _FP_PREV_SPAWN_GIZMOS_RELEASE(self)


SpawnGizmos.render = _finalpatch_spawn_gizmos_render
SpawnGizmos.release = _finalpatch_spawn_gizmos_release


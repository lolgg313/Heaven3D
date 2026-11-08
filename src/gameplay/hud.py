# @heaven3d-part gameplay/hud.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 14C —  HUD RENDERER  (play-mode overlay: crosshair, text, bars)
# #############################################################################
#
#  engine.hud lets blueprints draw a lightweight in-game HUD while a game
#  is playing (crosshair, health bars, ammo counters, messages).  It uses
#  the same flat-shader + font-texture technique as the editor UI but is a
#  completely separate overlay: the editor UI is untouched, and in
#  --runtime mode the HUD is the only overlay drawn.
#
#  Blueprints queue elements every frame inside on_tick; the engine
#  renders them after the 3D scene and clears the queue:
#      self.engine.hud.crosshair()
#      self.engine.hud.text(0.02, 0.02, "HP 100", size=24, color=(1,1,1,1))
#      self.engine.hud.bar(0.02, 0.06, 0.25, 0.02, frac, fg, bg)
#
#  Coordinates are normalised 0..1 with the ORIGIN AT THE TOP-LEFT
#  (y grows downward), which is the convention HUD authors expect.

class HUDRenderer:
    def __init__(self, ctx: moderngl.Context, width: int, height: int):
        self.ctx = ctx
        self.w = width
        self.h = height
        self.frame = 0
        self._elements: list = []
        self._fonts: Dict[int, FontAtlas] = {}
        self._prog = ShaderLib.get(ctx, "flat")
        # Unit quad (two triangles), each vertex = (pos.xy 0..1, uv.xy) —
        # identical layout to UIRenderer so the shared "flat" program works.
        verts = np.array([
            0, 1, 0, 1,
            0, 0, 0, 0,
            1, 1, 1, 1,
            0, 0, 0, 0,
            1, 0, 1, 0,
            1, 1, 1, 1,
        ], dtype=np.float32)
        self._quad_vbo = ctx.buffer(verts.tobytes())
        self._quad_vao = ctx.vertex_array(
            self._prog, [(self._quad_vbo, "2f 2f", "in_position", "in_uv")]) \
            if self._prog is not None else None

    # ------------------------------------------------------------ plumbing
    def resize(self, w: int, h: int):
        self.w, self.h = int(w), int(h)

    def _font(self, size: int) -> FontAtlas:
        size = int(_clamp(size, 8, 96))
        f = self._fonts.get(size)
        if f is None:
            f = FontAtlas(font_size=size)
            f.bind_ctx(self.ctx)
            self._fonts[size] = f
        # Dynamic HUD text (ammo counters) creates a texture per distinct
        # string; keep the cache bounded by flushing it when it grows big.
        if len(f._glyph_cache) > 160:
            for tex in f._glyph_cache.values():
                try:
                    tex.release()
                except Exception:
                    pass
            f._glyph_cache.clear()
            f._size_cache.clear()
        return f

    def clear(self):
        self._elements.clear()
        self.frame += 1

    # ------------------------------------------------------------ drawing API
    def rect(self, x: float, y: float, w: float, h: float,
             color=(1.0, 1.0, 1.0, 1.0)):
        """Filled rectangle, normalised top-left coords."""
        self._elements.append(("rect", float(x), float(y), float(w),
                               float(h), tuple(color)))

    def bar(self, x: float, y: float, w: float, h: float, frac: float,
            fg=(0.2, 0.9, 0.3, 1.0), bg=(0.08, 0.08, 0.08, 0.75),
            border=(0.0, 0.0, 0.0, 0.9)):
        """Progress bar (health, reload, charge...)."""
        frac = _clamp(float(frac), 0.0, 1.0)
        bw = 0.0015
        self.rect(x - bw, y - bw, w + 2 * bw, h + 2 * bw, border)
        self.rect(x, y, w, h, bg)
        if frac > 0.0:
            self.rect(x, y, w * frac, h, fg)

    def text(self, x: float, y: float, s: str, size: int = 20,
             color=(1.0, 1.0, 1.0, 1.0), shadow: bool = True):
        """Text at normalised top-left coords."""
        if not s:
            return
        self._elements.append(("text", float(x), float(y), str(s),
                               int(size), tuple(color), bool(shadow)))

    def center_text(self, y: float, s: str, size: int = 28,
                    color=(1.0, 1.0, 1.0, 1.0)):
        """Horizontally centred text at height y."""
        self._elements.append(("ctext", float(y), str(s), int(size),
                               tuple(color)))

    def crosshair(self, color=(1.0, 1.0, 1.0, 0.9), size: float = 0.010,
                  gap: float = 0.004, thickness: float = 0.0022,
                  spread: float = 0.0, dot: bool = True):
        """Classic 4-line FPS crosshair (+optional centre dot), centred on
        screen.  `spread` adds extra normalised gap (weapon bloom)."""
        cx, cy = 0.5, 0.5
        g = gap + max(0.0, float(spread))
        s = float(size)
        t = float(thickness)
        c = tuple(color)
        self.rect(cx - g - s, cy - t * 0.5, s, t, c)
        self.rect(cx + g, cy - t * 0.5, s, t, c)
        self.rect(cx - t * 0.5, cy - g - s, t, s, c)
        self.rect(cx - t * 0.5, cy + g, t, s, c)
        if dot:
            d = t * 0.9
            self.rect(cx - d * 0.5, cy - d * 0.5, d, d, c)

    def damage_vignette(self, intensity: float = 0.5):
        """Red screen-edge flash when the player is hurt."""
        a = _clamp(float(intensity), 0.0, 1.0) * 0.45
        if a <= 0.0:
            return
        c = (0.8, 0.05, 0.05, a)
        self.rect(0.0, 0.0, 1.0, 0.08, c)
        self.rect(0.0, 0.92, 1.0, 0.08, c)
        self.rect(0.0, 0.0, 0.06, 1.0, c)
        self.rect(0.94, 0.0, 0.06, 1.0, c)

    # ------------------------------------------------------------ render
    def render(self):
        """Draw everything queued this frame.  Called by the engine after
        the 3D scene; editor UI (if any) still draws afterwards on top."""
        if self._prog is None or self._quad_vao is None:
            self._elements.clear()
            return
        if not self._elements:
            self.clear()
            return
        ctx = self.ctx
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        SW, SH = float(max(self.w, 1)), float(max(self.h, 1))
        try:
            if "u_resolution" in self._prog:
                self._prog["u_resolution"] = (SW, SH)
            for el in self._elements:
                kind = el[0]
                if kind == "rect":
                    _, x, y, w, h, color = el
                    self._draw_quad((x * SW, (1.0 - y - h) * SH,
                                     w * SW, h * SH), color, None)
                elif kind == "text":
                    _, x, y, s, size, color, shadow = el
                    c255 = (int(_clamp(color[0], 0, 1) * 255),
                            int(_clamp(color[1], 0, 1) * 255),
                            int(_clamp(color[2], 0, 1) * 255))
                    font = self._font(size)
                    tex = font.render_text(s, c255)
                    if tex is None:
                        continue
                    tw, th = tex.size
                    px = x * SW
                    py = SH - y * SH - th
                    alpha = _clamp(color[3], 0.0, 1.0)
                    if shadow:
                        stex = font.render_text(s, (0, 0, 0))
                        if stex is not None:
                            self._draw_quad((px + 1.5, py - 1.5, tw, th),
                                            (1, 1, 1, alpha * 0.7), stex)
                    self._draw_quad((px, py, tw, th), (1, 1, 1, alpha), tex)
                elif kind == "ctext":
                    _, y, s, size, color = el
                    c255 = (int(_clamp(color[0], 0, 1) * 255),
                            int(_clamp(color[1], 0, 1) * 255),
                            int(_clamp(color[2], 0, 1) * 255))
                    font = self._font(size)
                    tex = font.render_text(s, c255)
                    if tex is None:
                        continue
                    tw, th = tex.size
                    px = (SW - tw) * 0.5
                    py = SH - y * SH - th
                    self._draw_quad((px, py, tw, th),
                                    (1, 1, 1, _clamp(color[3], 0, 1)), tex)
        finally:
            try:
                ctx.disable(moderngl.BLEND)
                ctx.enable(moderngl.DEPTH_TEST)
            except Exception:
                pass
        self.clear()

    def _draw_quad(self, rect_px, color, tex):
        x, y, w, h = rect_px
        if tex is not None:
            tex.use(location=0)
            self._prog["u_use_tex"] = 1
            self._prog["u_tex"] = 0
        else:
            self._prog["u_use_tex"] = 0
        # The HUD shares the cached "flat" program with the editor UI, which
        # sets u_radius for rounded buttons — always reset it to 0 here so
        # HUD elements (crosshair, bars) stay perfectly square.
        if "u_radius" in self._prog:
            self._prog["u_radius"] = 0.0
        self._prog["u_color"] = (float(color[0]), float(color[1]),
                                 float(color[2]), float(color[3]))
        self._prog["u_rect"] = (float(x), float(y), float(w), float(h))
        self._quad_vao.render(moderngl.TRIANGLES)

    def release(self):
        try:
            self._quad_vao.release()
        except Exception:
            pass
        try:
            self._quad_vbo.release()
        except Exception:
            pass


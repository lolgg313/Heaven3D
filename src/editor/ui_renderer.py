# @heaven3d-part editor/ui_renderer.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 13 —  UI RENDERER  (Unity-dark panels via GL quads)
# #############################################################################

class FontAtlas:
    """Builds a simple glyph atlas using pygame.font (no PIL dependency)."""
    def __init__(self, font_name: str = "Arial", font_size: int = 22):  # [FINALPATCH3:ui_scale_120]
        pygame.font.init()
        try:
            self.font = pygame.font.SysFont(font_name, font_size)
        except Exception:
            self.font = pygame.font.Font(None, font_size)
        self.size = font_size
        self._glyph_cache: Dict[str, moderngl.Texture] = {}
        self._size_cache: Dict[str, Tuple[int, int]] = {}
        self._ctx: Optional[moderngl.Context] = None

    def bind_ctx(self, ctx: moderngl.Context):
        self._ctx = ctx

    def text_size(self, text: str) -> Tuple[int, int]:
        """Return (width, height) in pixels for `text` without rendering."""
        if text in self._size_cache:
            return self._size_cache[text]
        try:
            w, h = self.font.size(text)
        except Exception:
            w, h = len(text) * self.size, self.size
        self._size_cache[text] = (w, h)
        return (w, h)

    def render_text(self, text: str, color=(230, 230, 230)) -> Optional[moderngl.Texture]:
        """Render a string to a texture (cached by text+color)."""
        if self._ctx is None:
            return None
        key = (text, color)
        if key in self._glyph_cache:
            return self._glyph_cache[key]
        try:
            surf = self.font.render(text, True, color)
            # pygame.image.tostring(..., 1) already flips the surface vertically
            # to match OpenGL's bottom-left origin. Do NOT also call
            # transform.flip() here — two flips cancel and the text renders
            # upside down.
            data = pygame.image.tostring(surf, "RGBA", 1)
            tex = self._ctx.texture(surf.get_size(), 4, data)
            self._glyph_cache[key] = tex
            return tex
        except Exception as ex:
            Log.debug(f"Font render failed: {ex}")
            return None


class UIRect:
    """A rectangle in normalised screen space (0..1, origin top-left).
    The renderer converts these to pixels internally so the shader can
    produce aspect-correct output."""
    __slots__ = ("x", "y", "w", "h")
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def to_pixels(self, screen_w: int, screen_h: int) -> Tuple[float, float, float, float]:
        return (self.x * screen_w, self.y * screen_h,
                self.w * screen_w, self.h * screen_h)


# NOTE on scissor in ModernGL 5.x: GL_SCISSOR_TEST is NOT a valid flag for
# ctx.enable()/ctx.disable() — those only accept BLEND / DEPTH_TEST /
# CULL_FACE / RASTERIZER_DISCARD / PROGRAM_POINT_SIZE.  Passing the raw
# 0x0C11 enum corrupts ModernGL's internal enable-mask (its bit pattern
# aliases the BLEND bit) and silently turns alpha blending ON/OFF, which
# made every text label drawn afterwards render as a solid box (pygame
# font surfaces carry the text colour in RGB even where alpha == 0, so
# without blending the whole glyph quad becomes an opaque rectangle).
# The correct ModernGL 5.x API is the per-framebuffer scissor box:
#     ctx.scissor = (x, y, w, h)   # sets the box AND enables the test
#     ctx.scissor = None           # disables the test cleanly
# ScrollRegion clipping below uses only that API.


class ScrollRegion:
    """State for one scrollable panel region (Hierarchy / Inspector body).

    The engine calls UIRenderer.begin_scroll(name, viewport) before emitting
    the panel's contents and end_scroll() afterwards.  Text and buttons
    emitted in between are:
      * offset vertically by -self.offset  (so the panel "scrolls")
      * clipped to the viewport rectangle via OpenGL scissor
      * tracked for content height so the scrollbar thumb is sized correctly
    Wheel events routed into scroll_wheel() advance the offset when the
    cursor is inside the viewport.
    """
    __slots__ = ("viewport", "offset", "content_h", "target_offset",
                 "track_rect", "thumb_rect")
    def __init__(self):
        # viewport is a UIRect in normalised 0..1 coords (set at begin_scroll)
        self.viewport: Optional[UIRect] = None
        # Current and target scroll offset in normalised units.  target is
        # what the user is scrolling toward; offset eases toward it for a
        # smooth glide.
        self.offset = 0.0
        self.target_offset = 0.0
        # Height of all content emitted this frame, in normalised units.
        self.content_h = 0.0
        # Scrollbar geometry (normalised 0..1 coords), refreshed every frame
        # by _draw_scrollbar() so mouse hit-testing / thumb dragging always
        # uses the same rects that are on screen.  thumb_rect is None when
        # the content fits and there is no draggable thumb.
        self.track_rect: Optional[UIRect] = None
        self.thumb_rect: Optional[UIRect] = None


class UIRenderer:
    """Immediate-mode Unity-dark UI overlay with scrollable panels."""
    def __init__(self, ctx: moderngl.Context, width: int, height: int):
        self.ctx = ctx
        self.w = width
        self.h = height
        self.font = FontAtlas()
        self.font.bind_ctx(ctx)
        prog = ShaderLib.get(ctx, "flat")
        self._prog = prog
        # Unit quad (two triangles), each vertex = (pos.xy, uv.xy).
        verts = np.array([
            0, 1, 0, 1,
            0, 0, 0, 0,
            1, 1, 1, 1,
            0, 0, 0, 0,
            1, 0, 1, 0,
            1, 1, 1, 1,
        ], dtype=np.float32)
        self._quad_vbo = ctx.buffer(verts.tobytes())
        self._quad_vao = ctx.vertex_array(prog, [(self._quad_vbo, "2f 2f", "in_position", "in_uv")])
        self._text_vaos: List[moderngl.VertexArray] = []
        self._panel_rects: List[Tuple[UIRect, tuple]] = []
        # Each text/button is stored with the scroll region it was emitted
        # inside (or None if emitted outside any begin_scroll/end_scroll).
        # This lets render() apply the correct scissor clip per widget
        # instead of guessing from the rect's X coordinate (which fails for
        # e.g. toolbar buttons that happen to share an X range with a panel).
        self._text_rects: List[Tuple[UIRect, str, tuple, Optional[UIRect]]] = []
        self._button_rects: List[Tuple[UIRect, str, callable, tuple, Optional[UIRect]]] = []
        # Scrollbar quads are QUEUED (not drawn immediately) so they can be
        # rendered on top of every panel at the end of render().  Previously
        # they were drawn mid-build, straight after the 3D pass, and the
        # panels drawn afterwards covered them — scrollbars never appeared.
        self._scrollbar_rects: List[Tuple[UIRect, tuple]] = []
        # Scrollbar thumb-drag state (mouse held on a thumb).
        self._scrollbar_drag: Optional[ScrollRegion] = None
        self._scrollbar_drag_mouse_y = 0.0
        self._scrollbar_drag_offset = 0.0
        # Cursor position in 0..1 bottom-origin UI coords, sampled once per
        # frame in begin_frame().  Drives hover states for every interactive
        # widget (buttons, list rows, scrollbar thumbs).  (-1, -1) = unknown,
        # which matches nothing so nothing renders hovered.
        self._mouse: Tuple[float, float] = (-1.0, -1.0)
        # ---- Scroll state ----
        # Named regions persist across frames so scroll position is kept.
        # During _build_ui the engine wraps each scrollable panel body in
        # begin_scroll(name, viewport) ... end_scroll().
        self.scroll_regions: Dict[str, ScrollRegion] = {}
        # The currently-active region (set by begin_scroll, cleared by end_scroll)
        self._active_scroll: Optional[ScrollRegion] = None
        # Set the screen resolution uniform once (updated on resize).
        if prog is not None and "u_resolution" in prog:
            prog["u_resolution"] = (float(self.w), float(self.h))

    def resize(self, width: int, height: int):
        self.w, self.h = int(width), int(height)
        if self._prog is not None and "u_resolution" in self._prog:
            self._prog["u_resolution"] = (float(self.w), float(self.h))

    def begin_frame(self):
        self._panel_rects.clear()
        self._text_rects.clear()
        self._button_rects.clear()
        self._scrollbar_rects.clear()
        self._active_scroll = None
        # Sample the cursor once per frame for hover feedback.  pygame's
        # get_pos() is top-left origin; flip Y into the UI's bottom-origin
        # convention so UIRect.contains() works directly.
        try:
            mx, my = pygame.mouse.get_pos()
            self._mouse = (mx / max(1, self.w), 1.0 - my / max(1, self.h))
        except Exception:
            self._mouse = (-1.0, -1.0)
        # Reset content height for every region (will be re-measured during
        # the frame as text/buttons are emitted inside begin_scroll).
        for r in self.scroll_regions.values():
            r.content_h = 0.0

    # ---- scrollable regions ---------------------------------------------
    def begin_scroll(self, name: str, viewport: UIRect):
        """Start emitting content for a scrollable panel region.  Until
        end_scroll() is called, every text()/button() rect is shifted by
        the region's current scroll offset and clipped to `viewport`."""
        region = self.scroll_regions.get(name)
        if region is None:
            region = ScrollRegion()
            self.scroll_regions[name] = region
        region.viewport = viewport
        # NOTE: do NOT clamp the offset here.  content_h reflects the
        # PREVIOUS frame's measurement (it's recomputed at end_scroll),
        # so clamping now would lock the user at offset=0 on the first
        # frame and never let them scroll.  The clamp is applied in
        # end_scroll() once this frame's content has been measured, and
        # in scroll_wheel() against the known content_h.
        # Instant scroll (no easing) — matches Unity's snappy wheel feel.
        region.offset = region.target_offset
        self._active_scroll = region

    def end_scroll(self, content_bottom_y: float, content_top_y: Optional[float] = None):
        """Finalise a scroll region.  `content_bottom_y` is the normalised
        Y of the lowest content emitted; `content_top_y` (optional) is the
        Y of the highest content.  Content height = top - bottom, which
        sizes the scrollbar thumb for the NEXT frame."""
        if self._active_scroll is None or self._active_scroll.viewport is None:
            self._active_scroll = None
            return
        vp = self._active_scroll.viewport
        top = content_top_y if content_top_y is not None else vp.y + vp.h
        # Content height is the vertical extent of everything emitted,
        # regardless of where the viewport sits.
        self._active_scroll.content_h = max(0.0, top - content_bottom_y)
        # Now that this frame's content is measured, clamp the scroll
        # offset so the user can't scroll past the bottom (or above the top).
        max_off = max(0.0, self._active_scroll.content_h - vp.h)
        self._active_scroll.target_offset = _clamp(
            self._active_scroll.target_offset, 0.0, max_off)
        # Draw the scrollbar for this region (track + thumb).
        self._draw_scrollbar(self._active_scroll)
        self._active_scroll = None

    def scroll_wheel(self, mx: float, my: float, amount: float) -> bool:
        """Forward a mouse-wheel event to whichever region is under the
        cursor.  `mx, my` are in 0..1 normalised screen coords; `amount`
        is the raw pygame wheel delta (y: +1 = wheel up, -1 = wheel down).

        Returns True when the event was CONSUMED by the UI — i.e. the cursor
        is over a scrollable viewport (the region scrolls) OR over any UI
        panel at all.  The engine uses the return value to decide whether
        the camera is allowed to see the wheel: over a panel the wheel must
        NEVER dolly the 3D camera, it only moves the panel's scrollbar.

        Wheel UP scrolls toward the top of the content (offset decreases).
        Wheel DOWN scrolls toward the bottom (offset increases).  We do
        NOT clamp here — content_h may be stale (it's measured at
        end_scroll of the previous frame, and zero before the first
        frame).  The clamp happens in end_scroll once content is measured."""
        for region in self.scroll_regions.values():
            if region.viewport is not None and region.viewport.contains(mx, my):
                # Negate so wheel-down (+1 in UI y, which grows downward)
                # increases the offset (shows lower content).
                region.target_offset -= amount * 0.05
                # Clamp only against content_h if it's already measured.
                if region.content_h > 0:
                    max_off = max(0.0, region.content_h - region.viewport.h)
                    region.target_offset = _clamp(
                        region.target_offset, 0.0, max_off)
                else:
                    region.target_offset = max(0.0, region.target_offset)
                return True
        # Not over a scrollable viewport: swallow the wheel anyway when the
        # cursor sits on a UI panel (toolbar / console / panel padding), so
        # scrolling while browsing panels never moves the 3D camera.
        return self.point_in_panels(mx, my)

    def point_in_panels(self, mx: float, my: float) -> bool:
        """True if (mx, my) in 0..1 coords is inside ANY UI panel rectangle
        (toolbar, hierarchy, inspector, console).  Used to stop clicks and
        wheel events from bleeding through the UI into the 3D scene."""
        for rect, _color in self._panel_rects:
            if rect.contains(mx, my):
                return True
        return False

    # ---- scrollbar mouse interaction ------------------------------------
    @property
    def scrollbar_dragging(self) -> bool:
        return self._scrollbar_drag is not None

    def scrollbar_hit(self, mx: float, my: float) -> Optional[ScrollRegion]:
        """Return the region whose scrollbar track/thumb is under (mx, my)
        in 0..1 coords, or None.  The thumb is tested first (it sits on top
        of the track)."""
        for region in self.scroll_regions.values():
            if region.thumb_rect is not None and region.thumb_rect.contains(mx, my):
                return region
            if region.track_rect is not None and region.track_rect.contains(mx, my):
                return region
        return None

    def scrollbar_mouse_down(self, mx: float, my: float) -> bool:
        """Left mouse button pressed at (mx, my) in 0..1 coords.
        Returns True if a scrollbar was hit — the engine must then CONSUME
        the click (no scene picking, no buttons underneath).

        Behaviour (Unity-style):
          * press on the thumb → begin a drag (scroll follows the mouse)
          * press on the track above/below the thumb → page up / page down
        """
        region = self.scrollbar_hit(mx, my)
        if region is None or region.viewport is None:
            return False
        max_off = max(0.0, region.content_h - region.viewport.h)
        if (region.thumb_rect is not None
                and region.thumb_rect.contains(mx, my) and max_off > 0.0):
            # Grab the thumb.
            self._scrollbar_drag = region
            self._scrollbar_drag_mouse_y = my
            self._scrollbar_drag_offset = region.target_offset
        elif region.thumb_rect is not None and max_off > 0.0:
            # Track click → page toward the click.  UI y grows upward, so a
            # click above the thumb scrolls toward the top (offset down).
            page = region.viewport.h
            thumb_mid = region.thumb_rect.y + region.thumb_rect.h * 0.5
            if my > thumb_mid:
                region.target_offset -= page
            else:
                region.target_offset += page
            region.target_offset = _clamp(region.target_offset, 0.0, max_off)
            region.offset = region.target_offset
        # Even when nothing can scroll (content fits), the click on the
        # scrollbar strip is consumed so it can't bleed into the scene.
        return True

    def scrollbar_mouse_drag(self, mx: float, my: float) -> bool:
        """Mouse moved while a scrollbar thumb is held.  Maps the vertical
        mouse delta onto the thumb's travel range and updates the offset."""
        region = self._scrollbar_drag
        if region is None or region.viewport is None or region.thumb_rect is None:
            return False
        max_off = max(0.0, region.content_h - region.viewport.h)
        if max_off <= 0.0:
            return True
        # Travel = how far the thumb can move inside the track (normalised).
        travel = region.viewport.h - region.thumb_rect.h
        if travel > 1e-6:
            # y grows upward: dragging DOWN (dy < 0) must INCREASE the
            # offset (show lower content), so we negate.
            dy = my - self._scrollbar_drag_mouse_y
            region.target_offset = _clamp(
                self._scrollbar_drag_offset - (dy / travel) * max_off,
                0.0, max_off)
            region.offset = region.target_offset
        return True

    def scrollbar_mouse_up(self):
        """Left mouse button released — end any scrollbar drag."""
        self._scrollbar_drag = None

    def _apply_scroll(self, rect: UIRect) -> UIRect:
        """If a scroll region is active, return a new UIRect shifted by the
        scroll offset.  We NEVER drop a rect here — even ones currently
        outside the viewport must be emitted so that content-height
        accounting (which sizes the scrollbar thumb) measures the full
        extent of the content.  The render() pass applies hardware scissor
        clipping so off-screen rects cost nothing visible."""
        if self._active_scroll is None or self._active_scroll.viewport is None:
            return rect
        return UIRect(rect.x, rect.y + self._active_scroll.offset,
                      rect.w, rect.h)

    def _draw_scrollbar(self, region: ScrollRegion):
        """Queue a Unity-style scrollbar on the right edge of the region's
        viewport: a thin dark track and a thumb whose size reflects the
        visible/content ratio.  The quads are only APPENDED here; render()
        draws them after all panels/text/buttons so they stay visible."""
        if region.viewport is None or self._prog is None:
            return
        vp = region.viewport
        content = region.content_h
        visible = vp.h
        if content <= visible * 1.01:
            # Everything fits — Unity hides the scrollbar.  We still draw a
            # faint track so the user knows the panel can scroll if more
            # content arrives.  No draggable thumb in this state.
            track = UIRect(vp.x + vp.w - 0.006, vp.y, 0.006, vp.h)
            region.track_rect = track
            region.thumb_rect = None
            self._scrollbar_rects.append(
                (track, (0.08, 0.08, 0.085, 1.0), UI_SCROLL_RADIUS, False))
            return
        max_off = content - visible
        # Thumb covers visible/content of the track height.
        thumb_ratio = visible / content
        thumb_h = vp.h * thumb_ratio
        thumb_y_top = vp.y + vp.h - thumb_h   # top of thumb
        # When offset=0, thumb is at the top.  When offset=max_off, at bottom.
        thumb_y = thumb_y_top - (vp.h - thumb_h) * (region.offset / max_off)
        # Track (full viewport height, thin, right edge)
        track = UIRect(vp.x + vp.w - 0.006, vp.y, 0.006, vp.h)
        # Thumb (lighter)
        thumb = UIRect(vp.x + vp.w - 0.006, thumb_y, 0.006, thumb_h)
        # Publish the geometry for mouse hit-testing / dragging.
        region.track_rect = track
        region.thumb_rect = thumb
        # (rect, color, corner_radius, hoverable) — only the thumb reacts to
        # the cursor; the track stays static.
        self._scrollbar_rects.append(
            (track, (0.10, 0.10, 0.105, 1.0), UI_SCROLL_RADIUS, False))
        self._scrollbar_rects.append(
            (thumb, (0.38, 0.38, 0.40, 1.0), UI_SCROLL_RADIUS, True))

    # ---- primitive widgets ---------------------------------------------
    def panel(self, rect: UIRect, color: tuple = THEME["panel"]):
        # Panels are NOT subject to scroll clipping (they are the background)
        self._panel_rects.append((rect, color))

    def text(self, rect: UIRect, text: str, color: tuple = THEME["text"]):
        r = self._apply_scroll(rect)
        # Stash the viewport of whatever scroll region was active (None if
        # emitted outside any region) so render() knows what to clip to.
        vp = (self._active_scroll.viewport
              if self._active_scroll is not None else None)
        self._text_rects.append((r, text, color, vp))

    def button(self, rect: UIRect, label: str, callback: callable,
               color: tuple = THEME["button"], align: str = "center",
               text_color: Optional[tuple] = None,
               pad_x_px: float = 16.8, pad_y_px: float = 7.2):  # [FINALPATCH3:ui_scale_120]
        """A rounded, hoverable push-button / list row.

        align      "center" (toolbar, actions) or "left" (hierarchy rows,
                   inspector value fields — text hugs the left padding).
        text_color Label colour; defaults to THEME["text"].  Hovered buttons
                   always switch to THEME["text_bright"].
        pad_*_px   Inner padding in PIXELS (14–16px horizontal, 6–8px
                   vertical per the design spec) — the label never touches
                   the rounded edge."""
        r = self._apply_scroll(rect)
        vp = (self._active_scroll.viewport
              if self._active_scroll is not None else None)
        self._button_rects.append(
            (r, label, callback, color, vp, align, text_color,
             pad_x_px, pad_y_px))

    def handle_click(self, mx: float, my: float) -> bool:
        """Dispatch a click at (mx, my) in 0..1 UV coords. Returns True if a button was hit."""
        for rect, label, callback, color, vp, *_style in self._button_rects:
            # Phantom-click guard: a button that belongs to a scroll region
            # but is currently scrolled OUTSIDE its viewport is invisible
            # (scissor-clipped).  It must not fire — otherwise clicks on
            # whatever is drawn at that spot (other panels, the toolbar)
            # trigger hidden buttons.
            if vp is not None and not vp.contains(mx, my):
                continue
            if rect.contains(mx, my):
                if callback:
                    try: callback()
                    except Exception as ex: Log.error(f"UI button '{label}': {ex}")
                return True
        return False

    # --------------------------------------------------------- draw helpers
    def _draw_rect(self, rect_px: Tuple[float, float, float, float],
                   color: tuple, tex: Optional[moderngl.Texture] = None,
                   tex_aspect: Optional[float] = None,
                   anchor_x: float = 0.0, anchor_y: float = 0.0,
                   radius: float = 0.0):
        """Draw one UI quad in pixel space.
        If `tex` and `tex_aspect` (w/h) are given, the quad is reshaped to the
        texture's aspect ratio inside the requested rect so text glyphs are
        never squashed/stretched.  The rect's height is preserved; width is
        derived from height * tex_aspect.
        `radius` is the corner radius in pixels (0 = square).  Rounding is
        anti-aliased in the fragment shader and skipped for texture quads."""
        if self._prog is None:
            return
        x, y, w, h = rect_px
        if tex is not None and tex_aspect is not None and tex_aspect > 0:
            # Fit the texture inside the rect preserving its aspect.
            natural_w = h * tex_aspect
            if natural_w <= w:
                # Centre horizontally
                x = x + (w - natural_w) * anchor_x
                w = natural_w
            else:
                # Texture wider than rect → fit by width, adjust height
                natural_h = w / tex_aspect
                y = y + (h - natural_h) * anchor_y
                h = natural_h
            tex.use(location=0)
            self._prog["u_use_tex"] = 1
            self._prog["u_tex"] = 0
            self._prog["u_color"] = (1, 1, 1, 1)
        else:
            self._prog["u_use_tex"] = 0
            self._prog["u_color"] = color
        self._prog["u_rect"] = (x, y, w, h)
        if "u_radius" in self._prog:
            self._prog["u_radius"] = float(radius)
        self._quad_vao.render(moderngl.TRIANGLES)

    def render(self):
        if self._prog is None:
            return
        self.ctx.disable(moderngl.DEPTH_TEST)
        # Enable alpha blending for the UI pass so font glyphs (and any
        # transparent panel corners) composite correctly over the 3D scene.
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA,
                               moderngl.ONE_MINUS_SRC_ALPHA)
        SW, SH = float(self.w), float(self.h)
        # Panels — fill the rect exactly (no aspect correction needed).
        for rect, color in self._panel_rects:
            self._draw_rect(rect.to_pixels(self.w, self.h), color)

        # ---- Text ----
        # Each text rect carries the viewport of the scroll region it was
        # emitted inside (or None).  We enable scissor only for in-region
        # text so widgets OUTSIDE regions (toolbar title/buttons) are never
        # accidentally clipped by a region whose X range they overlap.
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
        # ---- Buttons ----
        # Every button is a rounded rect with a hover lift (+13% background,
        # white label) and pixel padding around its label, per the theme.
        mx, my = self._mouse
        for (rect, label, callback, color, vp, align,
             text_color, pad_x, pad_y) in self._button_rects:
            if vp is not None:
                self._enable_scissor(vp)
            # Phantom-hover guard (same rule as handle_click): a row that is
            # scrolled outside its viewport is clipped — it must not light up.
            hovered = (rect.contains(mx, my)
                       and (vp is None or vp.contains(mx, my)))
            bg = _lighten(color, 0.13) if hovered else color
            rx, ry, rw, rh = rect.to_pixels(self.w, self.h)
            self._draw_rect((rx, ry, rw, rh), bg, radius=UI_RADIUS)
            tcol = THEME["text_bright"] if hovered \
                else (text_color or THEME["text"])
            tex = self.font.render_text(
                label, (int(tcol[0] * 255), int(tcol[1] * 255),
                        int(tcol[2] * 255)))
            if tex is not None:
                tw, th = tex.size
                # Inset the label by the pixel padding so glyphs keep clear
                # of the rounded edges (breathing room on all four sides).
                tx = rx + pad_x
                ty = ry + pad_y
                tw_avail = max(1.0, rw - 2.0 * pad_x)
                th_avail = max(1.0, rh - 2.0 * pad_y)
                self._draw_rect((tx, ty, tw_avail, th_avail), tcol,
                                tex=tex, tex_aspect=tw / max(1, th),
                                anchor_x=0.0 if align == "left" else 0.5,
                                anchor_y=0.5)
            if vp is not None:
                self._disable_scissor()

        # ---- Scrollbars (queued during end_scroll) ----
        # Drawn LAST so they sit on top of the panels.  Scissor is guaranteed
        # off here (every scissored widget above ended with _disable_scissor).
        for rect, color, radius, hoverable in self._scrollbar_rects:
            c = _lighten(color, 0.15) \
                if (hoverable and rect.contains(mx, my)) else color
            self._draw_rect(rect.to_pixels(self.w, self.h), c, radius=radius)

        # UI pass finished — disable blending again so it doesn't bleed into
        # the next frame's 3D deferred pipeline.
        self.ctx.disable(moderngl.BLEND)
        self.ctx.enable(moderngl.DEPTH_TEST)

    # ---- scissor helpers (hardware clipping for scroll regions) --------
    # ModernGL 5.x manages the scissor test per framebuffer through the
    # Context.scissor property: assigning a box enables the test, assigning
    # None disables it.  Do NOT reintroduce ctx.enable/disable(0x0C11) here —
    # that raw flag is not a valid ModernGL enable bit and corrupts the
    # internal state mask (it kills GL_BLEND, turning text into solid boxes).
    def _enable_scissor(self, viewport: UIRect):
        """Enable OpenGL scissor test for a normalised viewport rectangle.
        UIRect coordinates are BOTTOM-origin (y grows upward), which is the
        same convention glScissor uses — so NO Y-flip is applied.  (The old
        code flipped Y as if rects were top-origin; that shifted every clip
        box upward, so scrolled content painted over panel titles and the
        bottom rows were wrongly cut off.)"""
        px = int(viewport.x * self.w)
        py = int(viewport.y * self.h)
        pw = max(1, int(viewport.w * self.w))
        ph = max(1, int(viewport.h * self.h))
        # Assigning a box both sets it and enables GL_SCISSOR_TEST.
        self.ctx.scissor = (px, py, pw, ph)

    def _disable_scissor(self):
        # None disables GL_SCISSOR_TEST cleanly (no state leakage into the
        # next frame or the 3D pipeline).
        self.ctx.scissor = None

    def release(self):
        try: self._quad_vao.release()
        except Exception: pass
        try: self._quad_vbo.release()
        except Exception: pass


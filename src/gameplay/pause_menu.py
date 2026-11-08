# @heaven3d-part gameplay/pause_menu.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# =====================================================================
# [FINALPATCH2:esc_menu]
# Runtime-only ESC pause menu (Resume / Sound slider / Exit), built
# entirely from the engine's own HUDRenderer primitives - no assets, no
# new shaders.  This block only ships in the standalone runtime file;
# every wrapper additionally re-checks self.mode == "runtime", so the
# editor stays 100% untouched even if the block were ever copied into
# the engine file.  Append-only, double-install guarded, try/except
# everywhere with safe fallbacks.
# =====================================================================
if not globals().get("_FP2_ESC_INSTALLED"):
    _FP2_ESC_INSTALLED = True

    # Menu layout in normalised top-left coordinates: centred panel
    # 0.34 x 0.42, title y~0.30, RESUME y~0.40 (h 0.055), slider label
    # y~0.50 / track y~0.53 (h 0.03), EXIT y~0.62 (h 0.055); buttons
    # w 0.22, centred.
    _FP2_ESC_PANEL = (0.33, 0.29, 0.34, 0.42)
    _FP2_ESC_RESUME = (0.39, 0.40, 0.22, 0.055)
    _FP2_ESC_SLIDER = (0.39, 0.53, 0.22, 0.03)
    _FP2_ESC_EXIT = (0.39, 0.62, 0.22, 0.055)

    def _fp2_in_rect(mx, my, rect):
        x, y, w, h = rect
        return x <= mx <= x + w and y <= my <= y + h

    def _fp2_esc_resume(self):
        self._fp2_paused = False
        self._fp2_drag = False
        self._playing = getattr(self, "_fp2_was_playing", True)

    def _fp2_esc_set_vol(self, mx):
        x, _y, w, _h = _FP2_ESC_SLIDER
        vol = _clamp((float(mx) - x) / max(w, 1e-6), 0.0, 1.0)
        self._fp2_vol = vol
        try:
            self.audio.set_master_volume(vol)
        except Exception:
            pass

    def _fp2_esc_draw(self):
        hud = self.hud
        # Live slider dragging reads the held mouse state every frame.
        if getattr(self, "_fp2_drag", False) and self.input.lmb:
            _fp2_esc_set_vol(self, self.input.mouse_pos[0]
                             / float(max(self.width, 1)))
        vol = getattr(self, "_fp2_vol", None)
        if vol is None:   # lazily initialise from the audio system
            vol = float(getattr(self.audio, "master_volume", 1.0))
        vol = _clamp(float(vol), 0.0, 1.0)
        self._fp2_vol = vol
        mx = self.input.mouse_pos[0] / float(max(self.width, 1))
        my = self.input.mouse_pos[1] / float(max(self.height, 1))
        hud.rect(0.0, 0.0, 1.0, 1.0, (0.02, 0.03, 0.06, 0.55))
        px, py, pw, ph = _FP2_ESC_PANEL
        hud.rect(px, py, pw, ph, (0.07, 0.09, 0.14, 0.92))
        hud.center_text(0.305, "PAUSED", 34, (0.95, 0.96, 1.0, 1.0))
        for rect, label, ty in ((_FP2_ESC_RESUME, "RESUME", 0.414),
                                (_FP2_ESC_EXIT, "EXIT", 0.634)):
            c = ((0.32, 0.58, 0.96, 0.95)
                 if _fp2_in_rect(mx, my, rect)
                 else (0.16, 0.24, 0.38, 0.95))
            hud.rect(rect[0], rect[1], rect[2], rect[3], c)
            hud.center_text(ty, label, 22, (1.0, 1.0, 1.0, 1.0))
        hud.center_text(0.495, "SOUND  %d%%" % int(round(vol * 100.0)),
                        18, (0.85, 0.88, 0.95, 1.0))
        sx, sy, sw, sh = _FP2_ESC_SLIDER
        hud.bar(sx, sy, sw, sh, vol,
                fg=(0.30, 0.75, 0.42, 1.0),
                bg=(0.10, 0.12, 0.18, 0.90),
                border=(0.0, 0.0, 0.0, 0.90))
        # The HUD clears its queue after render, so everything above is
        # re-queued every frame while paused.
        hud.render()

    # ---- 1) ESC key toggles the pause menu ---------------------------
    _FP2_PREV_ON_KEY = HeavenEngine._on_key

    def _fp2_on_key(self, key):
        try:
            if getattr(self, "mode", None) == "runtime" \
                    and key == pygame.K_ESCAPE \
                    and (getattr(self, "_playing", False)
                         or getattr(self, "_fp2_paused", False)):
                if getattr(self, "_fp2_paused", False):
                    _fp2_esc_resume(self)
                else:
                    # PAUSE: clearing _playing freezes the scheduler,
                    # every blueprint tick and physics (the main loop
                    # gates all of them on self._playing), and makes
                    # _apply_mouse_capture release the mouse grab, so the
                    # cursor appears for the menu.
                    self._fp2_paused = True
                    self._fp2_was_playing = self._playing
                    self._playing = False
                    self._fp2_drag = False
                    if getattr(self, "_fp2_vol", None) is None:
                        self._fp2_vol = float(getattr(
                            self.audio, "master_volume", 1.0))
                return  # consume ESC while playing / paused
        except Exception:
            pass
        return _FP2_PREV_ON_KEY(self, key)

    HeavenEngine._on_key = _fp2_on_key

    # ---- 2) mouse: menu interaction while paused ---------------------
    _FP2_PREV_MOUSE_DOWN = HeavenEngine._on_mouse_down

    def _fp2_on_mouse_down(self, button, pos):
        try:
            if getattr(self, "mode", None) == "runtime" \
                    and getattr(self, "_fp2_paused", False):
                if button == 1:
                    self.input.lmb = True
                    mx = pos[0] / float(max(self.width, 1))
                    my = pos[1] / float(max(self.height, 1))
                    if _fp2_in_rect(mx, my, _FP2_ESC_RESUME):
                        _fp2_esc_resume(self)
                    elif _fp2_in_rect(mx, my, _FP2_ESC_EXIT):
                        # Clean shutdown through the stock path: the QUIT
                        # event ends the main loop -> release() ->
                        # pygame.quit(); the runtime's atexit 10 s wait
                        # then fires normally.
                        pygame.event.post(pygame.event.Event(pygame.QUIT))
                    elif _fp2_in_rect(mx, my, _FP2_ESC_SLIDER):
                        self._fp2_drag = True
                        _fp2_esc_set_vol(self, mx)
                # Consume EVERY click while paused so it can never reach
                # gameplay input (lmb_just_pressed) or pointer re-capture.
                return
        except Exception:
            pass
        return _FP2_PREV_MOUSE_DOWN(self, button, pos)

    HeavenEngine._on_mouse_down = _fp2_on_mouse_down

    _FP2_PREV_MOUSE_UP = HeavenEngine._on_mouse_up

    def _fp2_on_mouse_up(self, button, pos):
        try:
            if getattr(self, "mode", None) == "runtime" \
                    and getattr(self, "_fp2_paused", False):
                if button == 1:
                    self.input.lmb = False
                    self._fp2_drag = False
                return
        except Exception:
            pass
        return _FP2_PREV_MOUSE_UP(self, button, pos)

    HeavenEngine._on_mouse_up = _fp2_on_mouse_up

    # ---- 3) drawing: ride the every-frame outline hook ---------------
    _FP2_PREV_SELECTION_OUTLINE = HeavenEngine._render_selection_outline

    def _fp2_render_selection_outline(self):
        # _render_selection_outline is called EVERY frame in all modes
        # (the stock implementation no-ops in runtime), which makes it
        # the ideal draw hook: while paused the stock HUD path (gated on
        # _playing) never runs, so the menu renders itself here.
        _FP2_PREV_SELECTION_OUTLINE(self)
        try:
            if getattr(self, "mode", None) != "runtime":
                return
            if not getattr(self, "_fp2_paused", False):
                return
            if getattr(self, "hud", None) is None:
                return
            _fp2_esc_draw(self)   # HUD no-ops safely without its shader
        except Exception:
            pass

    HeavenEngine._render_selection_outline = _fp2_render_selection_outline
# [FINALPATCH2:esc_menu END]


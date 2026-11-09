# @heaven3d-part rendering/renderer.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 11 —  RENDERER  (deferred pipeline)
# #############################################################################

class Renderer:
    """
    The deferred renderer.  Owns the G-buffer, shadow map, and all
    post-processing framebuffers.  Build once; resize() on window changes.
    """
    # G-buffer world-position format (see _build_framebuffers).
    _gpos_dtype = 'f4'


    def __init__(self, ctx: moderngl.Context, width: int, height: int):
        self.ctx = ctx
        self.w = int(width)
        self.h = int(height)

        # Quality toggles
        self.enable_shadows = True
        self.enable_ssao = True
        self.enable_bloom = True
        self.enable_fxaa = True
        # ---- MSAA (hardware antialiasing on the geometry pass) ----
        # Stacked WITH FXAA: MSAA smooths true geometric edges at the source,
        # FXAA 3.11 then cleans up the edges MSAA can't see (texture-alpha
        # cutouts, shadow-map edges, specular/shader aliasing).
        # 4x is the cheap/quality sweet spot; set to 2 for weaker GPUs.
        # Toggle at runtime (renderer.enable_msaa = False) — the renderer
        # rebuilds the G-buffer automatically on the next frame.
        self.enable_msaa = True
        self.msaa_samples = 4
        self.bloom_strength = 0.8
        self.bloom_threshold = 1.0
        self.bloom_knee = 0.5
        self.exposure = 1.0
        self.ibl_intensity = 1.0      # strength of the environment lighting
        self.ao_direct = 0.35         # partial AO on direct light (grounding)
        self.ssao_radius = 1.2        # ~1m contact-shadow reach (EEVEE-like)
        self.ssao_kernel_size = 48
        self.ssao_bias = 0.02
        self.shadow_map_size = 4096   # 4096² depth = 64 MB — well in budget

        # SSAO kernel (generated once)
        self._ssao_kernel = self._gen_ssao_kernel()

        # Default textures (1x1) so binding missing textures doesn't crash
        self._default_tex = self._make_default_texture()
        self._default_normal_tex = self._make_default_texture(
            (0.5, 0.5, 1.0, 1.0))  # neutral normal

        # SSAO rotation-noise texture (4x4, tiled across the screen)
        self._noise_tex = self._make_noise_texture()

        # Procedural equirect environment map — drives BOTH the sky and the
        # image-based lighting so reflections always match the background.
        self._env_tex = self._make_env_texture()

        # Texture cache for materials
        self._tex_cache: Dict[str, moderngl.Texture] = {}

        # Build all framebuffers + the fullscreen quad
        self._vram_logged = False
        self._quad = FullscreenQuad(ctx)
        self._build_framebuffers()
        self._ssao_kernel_buf = None  # built lazily

    # ----------------------------------------------------------------- build
    def _make_default_texture(self, color=(1.0, 1.0, 1.0, 1.0)) -> moderngl.Texture:
        tex = self.ctx.texture((1, 1), 4, dtype='f1')
        # color floats are 0..1 — convert to 0..255 bytes (the old code wrote
        # raw floats as uint8, so the "white" default was actually 1/255 ≈
        # black and poisoned anything that legitimately sampled it).
        tex.write(np.array([int(round(c * 255.0)) for c in color],
                           dtype='uint8').tobytes())
        return tex

    def _make_noise_texture(self) -> moderngl.Texture:
        """4x4 tile of random XY rotation vectors for SSAO kernel rotation."""
        rng = np.random.default_rng(7)
        data = np.zeros((4, 4, 4), dtype=np.float32)
        data[..., 0:2] = rng.uniform(-1.0, 1.0, (4, 4, 2))
        data[..., 2] = 0.0
        tex = self.ctx.texture((4, 4), 4, data.tobytes(), dtype='f4')
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        tex.repeat_x = True
        tex.repeat_y = True
        return tex

    def update_env_texture(self, sun_direction: glm.vec3):
        """Regenerate the procedural environment map each frame so the sky
        gradient and IBL match the sun's current direction.  This gives a
        true day / night cycle: bright blue daytime when the sun is high,
        warm sunset colours near the horizon, and dark blue night when the
        sun is below ground.  The sun spot is also baked into the env map
        so glossy surfaces reflect it at the correct azimuth/elevation."""
        W, H = 128, 64
        img = np.zeros((H, W, 4), dtype=np.float32)
        sun_dir = glm.normalize(sun_direction)
        sun_elev = -sun_dir.y   # elevation above horizon (1 = zenith, -1 = nadir)

        # ---- colour palettes that interpolate with sun elevation ----
        if sun_elev > -0.15:
            t = _clamp((sun_elev + 0.15) / 1.15, 0.0, 1.0)
            zenith   = np.array([0.15 + t*0.13, 0.25 + t*0.21, 0.50 + t*0.35], dtype=np.float32) * (0.35 + t*1.0)
            horizon  = np.array([0.70, 0.65, 0.58], dtype=np.float32) * (0.45 + t*0.75)
            warm     = np.array([1.00, 0.65, 0.35], dtype=np.float32) * (0.25 + t*0.85)
            ground_t = np.array([0.35, 0.32, 0.28], dtype=np.float32) * (0.45 + t*0.45)
            ground_b = np.array([0.15, 0.14, 0.12], dtype=np.float32) * (0.40 + t*0.40)
        else:
            t = _clamp((sun_elev + 1.0) / 0.85, 0.0, 1.0)
            zenith   = np.array([0.03, 0.03, 0.08], dtype=np.float32) * (0.4 + t*0.6)
            horizon  = np.array([0.05, 0.05, 0.10], dtype=np.float32) * (0.35 + t*0.65)
            warm     = np.array([0.10, 0.06, 0.04], dtype=np.float32) * 0.25
            ground_t = np.array([0.03, 0.03, 0.04], dtype=np.float32)
            ground_b = np.array([0.01, 0.01, 0.02], dtype=np.float32)

        for y in range(H):
            v = (y + 0.5) / H
            phi = (v - 0.5) * math.pi
            dy = math.sin(phi)
            if dy >= 0.0:
                t = dy ** 0.65
                col = horizon * (1.0 - t) + zenith * t
                band = max(0.0, 1.0 - abs(dy - 0.06) / 0.22) ** 2
                col = col * (1.0 - 0.45 * band) + warm * (0.45 * band)
            else:
                t = min(1.0, -dy * 2.2) ** 0.8
                col = ground_t * (1.0 - t) + ground_b * t
            img[y, :, 0:3] = col[None, :]
            img[y, :, 3] = 1.0

        # Bake sun spot into env map so reflections show the real sun position
        if sun_elev > -0.25:
            sun_u = math.atan2(sun_dir.z, sun_dir.x) / (2.0 * math.pi) + 0.5
            sun_v = math.asin(_clamp(sun_dir.y, -1.0, 1.0)) / math.pi + 0.5
            sun_px = int(sun_u * W) % W
            sun_py = int(sun_v * H)
            intensity = max(0.0, sun_elev + 0.25) * 12.0 + 2.0
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    py = sun_py + dy
                    px = (sun_px + dx) % W
                    if 0 <= py < H:
                        dist = math.sqrt(dx*dx + dy*dy)
                        falloff = max(0.0, 1.0 - dist / 3.0)
                        img[py, px, 0:3] += np.array([1.0, 0.92, 0.78], dtype=np.float32) * intensity * falloff

        self._env_tex.write(img.astype(np.float16).tobytes())
        self._env_tex.build_mipmaps()

    def _make_env_texture(self) -> moderngl.Texture:
        """Procedural equirect environment map (128x64, fp16, mipmapped).

        A physically-plausible daytime sky dome:
          * zenith → horizon atmospheric gradient (slightly HDR, > 1)
          * warm tint near the horizon
          * darker brown-grey ground hemisphere (bounce light)
        The lighting pass samples LOD ~5 for diffuse irradiance and a
        roughness-weighted LOD for specular reflections; the mip chain turns
        those samples into a cheap prefiltered environment (EEVEE-style IBL).
        The equirect mapping matches dir_to_equirect() in the shader:
            u = atan(z, x) / 2π + 0.5 ,  v = asin(y) / π + 0.5
        """
        W, H = 128, 64
        img = np.zeros((H, W, 4), dtype=np.float32)
        zenith   = np.array([0.28, 0.46, 0.85], dtype=np.float32) * 1.35
        horizon  = np.array([0.85, 0.80, 0.72], dtype=np.float32) * 1.15
        warm     = np.array([1.00, 0.75, 0.50], dtype=np.float32) * 1.1
        ground_t = np.array([0.42, 0.38, 0.34], dtype=np.float32) * 0.9
        ground_b = np.array([0.22, 0.20, 0.18], dtype=np.float32) * 0.8
        # NOTE: row 0 of an OpenGL texture upload is the BOTTOM row (v = 0),
        # which the shader maps to phi = -π/2 (straight down).
        for y in range(H):
            v = (y + 0.5) / H                    # 0 = bottom (down), 1 = top (up)
            phi = (v - 0.5) * math.pi            # elevation -π/2 .. π/2
            dy = math.sin(phi)                   # direction y component
            if dy >= 0.0:
                t = dy ** 0.65                   # gradient curve
                col = horizon * (1.0 - t) + zenith * t
                # warm band hugging the horizon
                band = max(0.0, 1.0 - abs(dy - 0.06) / 0.22) ** 2
                col = col * (1.0 - 0.45 * band) + warm * (0.45 * band)
            else:
                t = min(1.0, -dy * 2.2) ** 0.8
                col = ground_t * (1.0 - t) + ground_b * t
            img[y, :, 0:3] = col[None, :]
            img[y, :, 3] = 1.0
        # fp16 texture — numpy must hand over float16 bytes to match 'f2'.
        tex = self.ctx.texture((W, H), 4, img.astype(np.float16).tobytes(),
                               dtype='f2')
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        return tex

    def _gen_ssao_kernel(self) -> np.ndarray:
        rng = np.random.default_rng(42)
        kernel = []
        for i in range(64):
            v = rng.uniform(-1, 1, 3)
            v[2] = rng.uniform(0, 1)   # hemisphere
            v = v.astype(np.float32)
            v = glm.normalize(glm.vec3(*v)) * rng.uniform(0.1, 1.0)
            scale = i / 64.0
            scale = 0.1 + scale * scale * 0.9
            kernel.append((v.x * scale, v.y * scale, v.z * scale))
        return np.array(kernel, dtype=np.float32)

    def _build_framebuffers(self):
        w, h = self.w, self.h
        vram = 0  # running VRAM estimate in bytes (logged once at startup)
        def _tex(cw, ch, comp, dt):
            nonlocal vram
            bpp = comp * {'f1': 1, 'f2': 2, 'f4': 4}[dt]
            vram += cw * ch * bpp
            return self.ctx.texture((cw, ch), comp, dtype=dt)

        # ---- G-buffer (MRT) ----
        # World position is RGBA32F: half floats only hold ~0.125 m steps
        # at 128-256 m from the origin (0.25 m past 256 m), which turned
        # shadows into blocky staircases / striped bands and gave SSAO a
        # grid of lines on every surface away from the world origin.
        # The other attachments (normals, colours) are fine as RGBA16F.
        def _make_gpos():
            # RGBA32F if the driver renders to it, else the old RGBA16F.
            for dt in ((self._gpos_dtype, 'f2') if self._gpos_dtype != 'f2' else ('f2',)):
                try:
                    t = _tex(w, h, 4, dt)
                    fb = self.ctx.framebuffer([t])
                    fb.release()
                    self._gpos_dtype = dt
                    return t
                except Exception as ex:
                    Log.warn(f"G-buffer position as {dt} unavailable ({ex})")
            raise RuntimeError("no renderable G-buffer position format")
        self.g_position = _make_gpos()
        self.g_normal   = _tex(w, h, 4, 'f2')
        self.g_albedo   = _tex(w, h, 4, 'f2')
        self.g_material = _tex(w, h, 4, 'f2')
        self.g_emissive = _tex(w, h, 4, 'f2')   # full-colour HDR emissive
        self.g_depth    = self.ctx.depth_renderbuffer((w, h))
        vram += w * h * 4
        self.g_fbo = self.ctx.framebuffer(
            [self.g_position, self.g_normal, self.g_albedo, self.g_material,
             self.g_emissive],
            self.g_depth)

        # ---- MSAA G-buffer (hardware AA on geometric edges) ----
        # The geometry pass renders into these multisampled renderbuffers
        # (same formats as the G-buffer textures so the resolve is a clean
        # 1:1 blit), then copy_framebuffer() resolves all 5 MRT
        # attachments into the regular single-sample textures in ONE call.
        self.g_fbo_msaa = None
        self.g_msaa_color_rbs = []
        self.g_msaa_depth_rb = None
        self._msaa_samples_active = 0
        msaa_req = int(getattr(self, "msaa_samples", 0)) \
            if getattr(self, "enable_msaa", False) else 0
        if msaa_req > 0:
            samples = max(1, min(msaa_req, int(self.ctx.max_samples)))
            for _attempt in range(2):
                try:
                    self.g_msaa_color_rbs = [
                        self.ctx.renderbuffer((w, h), 4, samples=samples,
                                              dtype=(self._gpos_dtype if _i == 0
                                                     else 'f2'))
                        for _i in range(5)]
                    self.g_msaa_depth_rb = self.ctx.depth_renderbuffer(
                        (w, h), samples=samples)
                    self.g_fbo_msaa = self.ctx.framebuffer(
                        self.g_msaa_color_rbs, self.g_msaa_depth_rb)
                    self._msaa_samples_active = samples
                    vram += w * h * (8 * 5 + 4 + (8 if self._gpos_dtype == 'f4' else 0)) \
                        * samples  # 4xRGBA16F + position + depth
                    break
                except Exception as ex:
                    for rb in self.g_msaa_color_rbs:
                        try: rb.release()
                        except Exception: pass
                    try:
                        if self.g_msaa_depth_rb is not None:
                            self.g_msaa_depth_rb.release()
                    except Exception: pass
                    self.g_msaa_color_rbs = []
                    self.g_msaa_depth_rb = None
                    self.g_fbo_msaa = None
                    self._msaa_samples_active = 0
                    if _attempt == 0 and self._gpos_dtype != 'f2':
                        # Multisampled RGBA32F refused: keep MSAA, fall back
                        # to an RGBA16F position (the resolve needs identical
                        # formats, so the single-sample one follows).
                        Log.warn(f"MSAA {samples}x with RGBA32F position "
                                 f"unavailable ({ex}); using RGBA16F.")
                        self._gpos_dtype = 'f2'
                        try:
                            self.g_fbo.release()
                            self.g_position.release()
                        except Exception:
                            pass
                        self.g_position = _tex(w, h, 4, 'f2')
                        self.g_fbo = self.ctx.framebuffer(
                            [self.g_position, self.g_normal, self.g_albedo,
                             self.g_material, self.g_emissive],
                            self.g_depth)
                        continue
                    # Driver refused — disable so we don't retry every frame.
                    Log.warn(f"MSAA {samples}x unavailable ({ex}); "
                             f"running with FXAA only.")
                    self.msaa_samples = 0
                    break

        # ---- HDR scene (lighting output) ----
        self.scene_color = _tex(w, h, 3, 'f2')
        self.scene_depth = self.ctx.depth_renderbuffer((w, h))
        vram += w * h * 4
        self.scene_fbo = self.ctx.framebuffer([self.scene_color], self.scene_depth)
        # Same colour target but with the G-BUFFER's depth attached — the
        # forward transparency pass draws into this FBO so glass occludes /
        # is occluded by opaque geometry without a depth copy.  (Sharing
        # attachments between FBOs is plain OpenGL; releasing this FBO does
        # not free the shared texture/renderbuffer.)
        self.scene_fbo_depth = self.ctx.framebuffer([self.scene_color],
                                                    self.g_depth)

        # ---- SSAO (raw + blurred) ----
        self.ssao_color = _tex(w, h, 1, 'f1')
        self.ssao_fbo = self.ctx.framebuffer([self.ssao_color])
        self.ssao_blur_tex = _tex(w, h, 1, 'f1')
        self.ssao_blur_fbo = self.ctx.framebuffer([self.ssao_blur_tex])

        # ---- Bloom: bright extract + half-res ping/pong + quarter-res
        # ping/pong (the second, blurrier octave gives the wide soft EEVEE
        # glow around bright sources) ----
        bw, bh = max(1, w // 2), max(1, h // 2)
        qw, qh = max(1, w // 4), max(1, h // 4)
        self.bloom_bright = _tex(bw, bh, 3, 'f2')
        self.bloom_bright_fbo = self.ctx.framebuffer([self.bloom_bright])
        self.bloom_ping = _tex(bw, bh, 3, 'f2')
        self.bloom_ping_fbo = self.ctx.framebuffer([self.bloom_ping])
        self.bloom_pong = _tex(bw, bh, 3, 'f2')
        self.bloom_pong_fbo = self.ctx.framebuffer([self.bloom_pong])
        self.bloom_ping2 = _tex(qw, qh, 3, 'f2')
        self.bloom_ping2_fbo = self.ctx.framebuffer([self.bloom_ping2])
        self.bloom_pong2 = _tex(qw, qh, 3, 'f2')
        self.bloom_pong2_fbo = self.ctx.framebuffer([self.bloom_pong2])

        # ---- Shadow map ----
        sms = int(self.shadow_map_size)
        self.shadow_map = self.ctx.depth_texture((sms, sms))
        # Explicitly enable hardware depth-comparison so the lighting pass
        # can sample it with sampler2DShadow → FREE bilinear PCF on every
        # tap (this is the correct, driver-portable way; sampling a depth
        # texture with a plain sampler2D is undefined when compare mode is
        # on and returned garbage/black shadows on several drivers).
        self.shadow_map.compare_func = '<='
        self.shadow_map.filter = (moderngl.LINEAR, moderngl.LINEAR)
        vram += sms * sms * 4
        # ModernGL requires explicit empty color_attachments for depth-only FBO
        self.shadow_fbo = self.ctx.framebuffer(
            color_attachments=[], depth_attachment=self.shadow_map)

        # ---- Final LDR output ----
        self.final_color = _tex(w, h, 3, 'f1')
        self.final_fbo = self.ctx.framebuffer([self.final_color])

        self._vram_estimate_bytes = vram
        if not getattr(self, "_vram_logged", False):
            self._vram_logged = True
            Log.info(f"Renderer framebuffer budget: ~{vram / 1e6:.0f} MB VRAM "
                     f"(target < 1024 MB) @ {w}x{h}, shadow {sms}px")

    def _release_framebuffers(self):
        """Release everything _build_framebuffers() created.  Shared by
        resize() and the runtime MSAA toggle."""
        for fbo in [getattr(self, "g_fbo", None),
                    getattr(self, "g_fbo_msaa", None),
                    getattr(self, "scene_fbo", None),
                    getattr(self, "scene_fbo_depth", None),
                    getattr(self, "ssao_fbo", None),
                    getattr(self, "ssao_blur_fbo", None),
                    getattr(self, "bloom_bright_fbo", None),
                    getattr(self, "bloom_ping_fbo", None),
                    getattr(self, "bloom_pong_fbo", None),
                    getattr(self, "bloom_ping2_fbo", None),
                    getattr(self, "bloom_pong2_fbo", None),
                    getattr(self, "final_fbo", None),
                    getattr(self, "shadow_fbo", None)]:
            try:
                if fbo is not None: fbo.release()
            except Exception: pass
        for tex in [getattr(self, "g_position", None),
                    getattr(self, "g_normal", None),
                    getattr(self, "g_albedo", None),
                    getattr(self, "g_material", None),
                    getattr(self, "g_emissive", None),
                    getattr(self, "scene_color", None),
                    getattr(self, "ssao_color", None),
                    getattr(self, "ssao_blur_tex", None),
                    getattr(self, "bloom_bright", None),
                    getattr(self, "bloom_ping", None),
                    getattr(self, "bloom_pong", None),
                    getattr(self, "bloom_ping2", None),
                    getattr(self, "bloom_pong2", None),
                    getattr(self, "final_color", None),
                    getattr(self, "shadow_map", None)]:
            try:
                if tex is not None: tex.release()
            except Exception: pass
        for rb in ([getattr(self, "g_depth", None),
                    getattr(self, "scene_depth", None),
                    getattr(self, "g_msaa_depth_rb", None)]
                   + list(getattr(self, "g_msaa_color_rbs", []) or [])):
            try:
                if rb is not None: rb.release()
            except Exception: pass
        self.g_fbo_msaa = None
        self.g_msaa_color_rbs = []
        self.g_msaa_depth_rb = None
        self._msaa_samples_active = 0

    def resize(self, width: int, height: int):
        if width == self.w and height == self.h:
            return
        self.w, self.h = int(width), int(height)
        self._release_framebuffers()
        self._build_framebuffers()

    # ------------------------------------------------------------- textures
    def get_texture(self, source) -> moderngl.Texture:
        """Resolve a texture source into a cached ModernGL texture.

        `source` may be:
            None                                → default white texture
            "/abs/path.png"  (str)              → file on disk (legacy)
            ("path", "/abs/path.png")           → file on disk (descriptor)
            ("bytes", b"<image bytes>")         → embedded image bytes

        Decoding is ROBUST: pygame's magic-byte sniffing handles PNG/JPEG/
        BMP/GIF, but several formats common inside GLBs have no magic bytes
        or need explicit decoders — TGA (very common in older exports), WebP
        (optimised web exports), TIFF, 16-bit PNG.  We therefore try a chain
        of decoders (pygame → pygame with extension hints → PIL → PIL with
        explicit formats) before giving up, instead of silently rendering
        the model texture-less."""
        if not source:
            return self._default_tex

        # Normalise the descriptor form into (kind, payload)
        if isinstance(source, str):
            kind, payload = "path", source
        elif isinstance(source, tuple) and len(source) == 2:
            kind, payload = source
        else:
            return self._default_tex

        # [TRIPLEA:p3_gif_get_texture_hook] Animated GIFs loop on the albedo
        # slot: .gif path descriptors are served from a per-frame texture
        # cache (Renderer._gif_texture_for, appended patch block).  Decode
        # failure falls through to the normal static path / default texture.
        if kind == "path" and str(payload).lower().endswith(".gif"):
            _gif_tex = self._gif_texture_for(payload)
            if _gif_tex is not None:
                return _gif_tex

        # Cache key: file path absolute, or a hash of the byte payload
        if kind == "path":
            cache_key = os.path.abspath(payload)
        else:
            cache_key = f"bytes:{hash(payload):x}"

        if cache_key in self._tex_cache:
            return self._tex_cache[cache_key]

        surf = self._decode_image(kind, payload)
        if surf is None:
            return self._default_tex
        try:
            # `tostring(..., 1)` returns RGBA in OpenGL's bottom-left origin.
            # (Do NOT also call transform.flip — two flips cancel and would
            # make the texture render upside down on the model.)
            data = pygame.image.tostring(surf, "RGBA", 1)
            tex = self.ctx.texture(surf.get_size(), 4, data)
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            try:
                # Anisotropic filtering — sharp textures at grazing angles
                # (ground planes especially).  4x is visually lossless and
                # costs no extra VRAM.
                tex.anisotropy = 4.0
            except Exception:
                pass
            self._tex_cache[cache_key] = tex
            return tex
        except Exception as ex:
            Log.warn(f"Texture upload failed ({kind}): {ex}")
            return self._default_tex

    # -------------------------------------------------- image decoding
    @staticmethod
    def _decode_image(kind: str, payload):
        """Decode an image into a pygame Surface, trying every decoder that
        might understand the format.  Returns None if nothing worked.

        pygame.image.load() sniffs PNG/JPEG/BMP/GIF by magic bytes, but:
          • TGA has NO magic bytes — from memory it needs an extension hint
          • WebP / TIFF / 16-bit PNG support varies by SDL_image build
        PIL (when installed) covers those; explicit format hints cover the
        magic-less rest.  This is what makes "some models import with no
        textures" stop happening."""
        import io
        # ---- file on disk: pygame uses the extension, so it handles TGA
        # and friends natively; PIL is the fallback for exotic encodings.
        if kind == "path":
            if not os.path.isfile(payload):
                Log.warn(f"Texture missing: {payload}")
                return None
            try:
                return pygame.image.load(payload)
            except Exception:
                pass
            try:
                from PIL import Image
                with Image.open(payload) as im:
                    return pygame.image.frombuffer(
                        im.convert("RGBA").tobytes(), im.size, "RGBA")
            except ImportError:
                Log.warn(f"Texture unreadable by pygame and PIL not installed: {payload}")
            except Exception as ex:
                Log.warn(f"Texture decode failed ({os.path.basename(payload)}): {ex}")
            return None
        # ---- embedded bytes ----
        # 1) pygame magic-byte sniff (PNG / JPEG / BMP / GIF)
        try:
            return pygame.image.load(io.BytesIO(payload))
        except Exception:
            pass
        # 2) pygame with an explicit extension hint (TGA has no magic bytes;
        #    WebP/TIFF depend on the SDL_image build)
        for hint in ("img.tga", "img.webp", "img.tiff", "img.png", "img.jpg"):
            try:
                return pygame.image.load(io.BytesIO(payload), hint)
            except Exception:
                pass
        # 3) PIL content sniff (covers WebP/TIFF/16-bit PNG/DDS on most builds)
        try:
            from PIL import Image
            try:
                with Image.open(io.BytesIO(payload)) as im:
                    return pygame.image.frombuffer(
                        im.convert("RGBA").tobytes(), im.size, "RGBA")
            except Exception:
                pass
            # 4) PIL with explicit formats for magic-less containers
            for fmt in ("TGA", "WEBP", "TIFF", "BMP"):
                try:
                    with Image.open(io.BytesIO(payload), formats=[fmt]) as im:
                        return pygame.image.frombuffer(
                            im.convert("RGBA").tobytes(), im.size, "RGBA")
                except Exception:
                    pass
        except ImportError:
            Log.warn("Embedded texture undecodable by pygame; install Pillow "
                     "for TGA/WebP/TIFF support.")
            return None
        Log.warn("Embedded texture could not be decoded by any decoder.")
        return None

    # -------------------------------------------------- light frustum fit
    def _fit_light_frustum(self, scene: Scene, light_view: glm.mat4) -> glm.mat4:
        """Fit the sun's orthographic frustum TIGHTLY around the scene's
        world-space bounding boxes (in light space).  A tight frustum spends
        every shadow-map texel on geometry that actually exists — this alone
        is worth several shadow-map resolution doublings (EEVEE does the same
        with its cascade fitting).  Falls back to a ±50 ortho for empty scenes."""
        mins = glm.vec3(float("inf"))
        maxs = glm.vec3(float("-inf"))
        found = False
        for node, mesh, model in scene.traverse_meshes():
            bb_min, bb_max = mesh.bounding_box
            for cx in (bb_min.x, bb_max.x):
                for cy in (bb_min.y, bb_max.y):
                    for cz in (bb_min.z, bb_max.z):
                        p = light_view * (model * glm.vec4(cx, cy, cz, 1.0))
                        mins = glm.min(mins, glm.vec3(p))
                        maxs = glm.max(maxs, glm.vec3(p))
                        found = True
        if not found:
            return glm.ortho(-50.0, 50.0, -50.0, 50.0, 0.1, 200.0)
        pad = 2.0
        left, right = mins.x - pad, maxs.x + pad
        bottom, top = mins.y - pad, maxs.y + pad
        # Stabilise: when anything moves the bounds change every frame, and
        # a window that rescales / slides by fractions of a texel makes all
        # shadow edges shimmer.  Grow the extents to coarse steps (texel
        # size stays constant until the scene really grows) and snap the
        # window to whole texels.  Only ever ENLARGES the fit slightly.
        try:
            n = max(256, int(self.shadow_map_size))

            def _stable(lo, hi):
                half = max((hi - lo) * 0.5, 0.5)
                # power-of-two step (~3% of the size): a discrete ladder,
                # so the size only changes when the scene crosses a step
                q = max(0.5, 2.0 ** math.floor(math.log2(half * 0.03 + 1e-9)))
                half = math.ceil(half / q) * q
                step = (2.0 * half) / n
                mid = math.floor(((lo + hi) * 0.5) / step + 0.5) * step
                return mid - half, mid + half
            left, right = _stable(left, right)
            bottom, top = _stable(bottom, top)
        except Exception:
            pass
        # Light looks down -Z in light space; depth = -z.  Give generous
        # depth range so off-screen casters still shadow the visible area.
        near = max(0.1, -maxs.z - 60.0)
        far = max(near + 1.0, -mins.z + 60.0)
        return glm.ortho(left, right, bottom, top, near, far)

    # --------------------------------------------------------- main render
    def render(self, scene: Scene, camera: Camera):
        """Render one frame:
        shadow → geometry (MSAA xN, resolved) → SSAO → SSAO blur →
        lighting(+IBL+AO) → forward transparency (BLEND) →
        bloom (2 octaves) → composite/tonemap → FXAA 3.11."""
        # --- sync sky to current sun position (day / night cycle) ---
        if scene.sun:
            self.update_env_texture(scene.sun.direction)
        # --- dynamic shadow-map resize if user changed sun settings ---
        if scene.sun and scene.sun.shadow_map_size != self.shadow_map_size:
            self.shadow_map_size = scene.sun.shadow_map_size
            try:
                self.shadow_map.release()
                self.shadow_fbo.release()
            except Exception:
                pass
            sms = int(self.shadow_map_size)
            self.shadow_map = self.ctx.depth_texture((sms, sms))
            self.shadow_map.compare_func = '<='
            self.shadow_map.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.shadow_fbo = self.ctx.framebuffer(
                color_attachments=[], depth_attachment=self.shadow_map)
        view = camera.view()
        proj = camera.projection()

        # =========================================================
        # 1) SHADOW PASS  (render depth from sun's POV, tight frustum)
        # =========================================================
        if self.enable_shadows and scene.sun:
            sun = scene.sun
            light_view = sun.view_matrix()
            light_proj = self._fit_light_frustum(scene, light_view)
            light_vp = light_proj * light_view
            shadow_prog = ShaderLib.get(self.ctx, "shadow")
            self.shadow_fbo.clear()
            self.shadow_fbo.use()
            # Enable depth test + write so the shadow map stores nearest depth
            self.ctx.enable(moderngl.DEPTH_TEST)
            self.ctx.depth_func = '<'   # standard less-than depth test
            if shadow_prog:
                shadow_prog["u_light_space"].write(light_vp.to_bytes())
                _casters = scene.traverse_meshes()
                _cull = globals().get("_h3d274_shadow_casters")   # [H3D274:perf]
                if _cull is not None:
                    try:
                        _casters = _cull(_casters, light_vp)
                    except Exception:
                        pass
                for node, mesh, model in _casters:
                    vao = mesh.get_vao(shadow_prog)
                    shadow_prog["u_model"].write(model.to_bytes())
                    if mesh.indexed:
                        vao.render(moderngl.TRIANGLES)
                    else:
                        vao.render(moderngl.TRIANGLES, vertices=mesh.vertex_count)
            # Stash for lighting pass
            self._light_vp = light_vp
        else:
            self._light_vp = glm.mat4(1.0)

        # =========================================================
        # 2) GEOMETRY PASS  (write G-buffer — via the multisampled
        #    G-buffer when MSAA is enabled, resolved right after)
        # =========================================================
        # --- live MSAA toggle / sample-count change -----------------
        # One int compare per frame; only rebuilds when the requested
        # state actually differs from what was built.
        if self.enable_msaa and int(self.msaa_samples) > 0:
            _want_msaa = max(1, min(int(self.msaa_samples),
                                    int(self.ctx.max_samples)))
        else:
            _want_msaa = 0
        if _want_msaa != self._msaa_samples_active:
            self._release_framebuffers()
            self._build_framebuffers()

        geom_prog = ShaderLib.get(self.ctx, "geometry")
        g_target = self.g_fbo_msaa if self.g_fbo_msaa is not None else self.g_fbo
        g_target.clear()
        g_target.use()
        # Enable depth test so closer geometry correctly occludes farther geometry
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.depth_func = '<'
        # [TRIPLEA:geom_pass_frustum_culling_instancing]
        # Per-frame renderer statistics (cheap ints, readable by tools/HUD):
        #   stat_draw_calls — vao.render calls issued this frame
        #   stat_culled     — meshes frustum-culled this frame
        #   stat_instanced  — meshes drawn through instanced draw calls
        self.stat_draw_calls = 0
        self.stat_culled = 0
        self.stat_instanced = 0
        if geom_prog:
            geom_prog["u_view"].write(view.to_bytes())
            geom_prog["u_proj"].write(proj.to_bytes())

            # ---- Frustum culling (Gribb-Hartmann) ----------------------
            # Six world-space planes extracted from proj*view.  A mesh is
            # culled only when ALL 8 corners of its world-transformed AABB
            # lie outside ANY single plane (p-vertex test) — conservative,
            # it never pops.  The shadow pass is deliberately NOT culled:
            # off-screen casters must still cast shadows into the frame.
            _frustum_planes = None
            try:
                _vp = proj * view
                _row = [[_vp[c][r] for c in range(4)] for r in range(4)]
                _frustum_planes = []
                for _ci, _sgn in ((0, 1.0), (0, -1.0), (1, 1.0),
                                  (1, -1.0), (2, 1.0), (2, -1.0)):
                    _p = [_row[3][i] + _sgn * _row[_ci][i] for i in range(4)]
                    _ln = math.sqrt(_p[0] * _p[0] + _p[1] * _p[1] +
                                    _p[2] * _p[2])
                    if _ln > 1e-12:
                        _frustum_planes.append((_p[0] / _ln, _p[1] / _ln,
                                                _p[2] / _ln, _p[3] / _ln))
                if len(_frustum_planes) != 6:
                    _frustum_planes = None
            except Exception:
                _frustum_planes = None

            def _aabb_in_frustum(model, bb):
                """True when the world-space AABB of local box `bb` under
                `model` may intersect the camera frustum."""
                _bmin, _bmax = bb
                _wmin = [model[3][0], model[3][1], model[3][2]]
                _wmax = [_wmin[0], _wmin[1], _wmin[2]]
                for _a in range(3):
                    for _b in range(3):
                        _e = model[_b][_a]
                        if _e >= 0.0:
                            _wmin[_a] += _e * _bmin[_b]
                            _wmax[_a] += _e * _bmax[_b]
                        else:
                            _wmin[_a] += _e * _bmax[_b]
                            _wmax[_a] += _e * _bmin[_b]
                for _nx, _ny, _nz, _d in _frustum_planes:
                    _px = _wmax[0] if _nx >= 0.0 else _wmin[0]
                    _py = _wmax[1] if _ny >= 0.0 else _wmin[1]
                    _pz = _wmax[2] if _nz >= 0.0 else _wmin[2]
                    if _nx * _px + _ny * _py + _nz * _pz + _d < 0.0:
                        return False
                return True

            # ---- Collect the visible OPAQUE draws -----------------------
            _visible = []
            for node, mesh, model in scene.traverse_meshes():
                mat = node.material if node.material else Material()
                # BLEND materials are deferred-incompatible: they draw in the
                # forward transparency pass after lighting instead.
                if (mat.alpha_mode or "OPAQUE").upper() == "BLEND":
                    continue
                if _frustum_planes is not None:
                    try:
                        if not _aabb_in_frustum(model, mesh.bounding_box):
                            self.stat_culled += 1
                            continue
                    except Exception:
                        pass  # culling must never swallow a visible mesh
                _visible.append((mesh, mat, model))

            # ---- Group identical draws for GPU instancing ---------------
            # Key: same Mesh object AND same Material object → byte-identical
            # GL state apart from the per-instance model matrix.
            _groups = {}
            _draw_order = []
            for mesh, mat, model in _visible:
                _key = (id(mesh), id(mat))
                _grp = _groups.get(_key)
                if _grp is None:
                    _grp = (mesh, mat, [])
                    _groups[_key] = _grp
                    _draw_order.append(_grp)
                _grp[2].append(model)

            # Instanced geometry program (compiled lazily by the ShaderLib
            # hook appended near the end of this module; None → legacy).
            try:
                _inst_prog = ShaderLib.get(self.ctx, "geometry_inst")
            except Exception:
                _inst_prog = None
            if _inst_prog is not None:
                try:
                    _inst_prog["u_view"].write(view.to_bytes())
                    _inst_prog["u_proj"].write(proj.to_bytes())
                except Exception:
                    _inst_prog = None
            if not hasattr(self, "_inst_vao_cache"):
                self._inst_vao_cache = {}

            for mesh, mat, models in _draw_order:
                # Instanced fast path: >=4 identical draws share one call.
                if (_inst_prog is not None and len(models) >= 4
                        and getattr(mesh, "vbo", None) is not None):
                    try:
                        _n = len(models)
                        _ckey = (id(mesh), id(_inst_prog))
                        _entry = self._inst_vao_cache.get(_ckey)
                        if _entry is None or _entry[2] < _n:
                            # (Re)build the instance VBO + VAO with headroom.
                            if _entry is not None:
                                try:
                                    _entry[0].release()
                                except Exception:
                                    pass
                                try:
                                    _entry[1].release()
                                except Exception:
                                    pass
                            _cap = max(64, _n * 2)
                            _ivbo = self.ctx.buffer(reserve=_cap * 64)
                            # Same interleaved vertex spec as Mesh.get_vao,
                            # plus one column-major mat4 per instance
                            # ("/i" divisor → one element per instance).
                            _ivao = self.ctx.vertex_array(
                                _inst_prog,
                                [(mesh.vbo, "3f 3f 4f 2f",
                                  "in_position", "in_normal",
                                  "in_tangent", "in_uv"),
                                 (_ivbo, "16f /i", "i_model")],
                                index_buffer=(mesh.ibo
                                              if mesh.indexed else None))
                            _entry = (_ivao, _ivbo, _cap)
                            self._inst_vao_cache[_ckey] = _entry
                        _ivao, _ivbo, _cap = _entry
                        _ivbo.orphan(_cap * 64)
                        _ivbo.write(b"".join(
                            _m.to_bytes() for _m in models))
                        self._bind_material(_inst_prog, mat)
                        if mesh.indexed:
                            _ivao.render(moderngl.TRIANGLES, instances=_n)
                        else:
                            _ivao.render(moderngl.TRIANGLES,
                                         vertices=mesh.vertex_count,
                                         instances=_n)
                        self.stat_draw_calls += 1
                        self.stat_instanced += _n
                        continue
                    except Exception:
                        pass  # fall through to the legacy per-mesh path
                # Legacy per-mesh path (also the safety fallback).
                self._bind_material(geom_prog, mat)
                vao = mesh.get_vao(geom_prog)
                for model in models:
                    geom_prog["u_model"].write(model.to_bytes())
                    if mesh.indexed:
                        vao.render(moderngl.TRIANGLES)
                    else:
                        vao.render(moderngl.TRIANGLES, vertices=mesh.vertex_count)
                    self.stat_draw_calls += 1

        # Mark background pixels in g_position.w = 0 (geometry writes w=1)
        # We do this implicitly: g_fbo.clear sets all to 0; geometry writes w=1.

        # --- MSAA resolve: one blit resolves all 5 MRT attachments ---
        if self.g_fbo_msaa is not None:
            self.ctx.copy_framebuffer(self.g_fbo, self.g_fbo_msaa)

        # =========================================================
        # 3) SSAO  (computed BEFORE lighting so the lighting pass can
        #           multiply ambient/IBL by the occlusion — previously the
        #           AO texture was generated and then thrown away)
        # =========================================================
        ssao_result = self._default_tex   # white (no occlusion) fallback
        if self.enable_ssao:
            ssao_prog = ShaderLib.get(self.ctx, "ssao")
            if ssao_prog:
                self.ssao_fbo.clear(1.0, 1.0, 1.0, 1.0)
                self.ssao_fbo.use()
                self.ctx.disable(moderngl.DEPTH_TEST)
                self.g_position.use(location=0)
                ssao_prog["g_position"] = 0
                self.g_normal.use(location=1)
                ssao_prog["g_normal"] = 1
                self._noise_tex.use(location=2)
                ssao_prog["u_noise"] = 2
                ssao_prog["u_noise_scale"] = (self.w / 4.0, self.h / 4.0)
                ssao_prog["u_proj"].write(proj.to_bytes())
                ssao_prog["u_view"].write(view.to_bytes())
                ssao_prog["u_radius"] = self.ssao_radius
                ssao_prog["u_bias"] = self.ssao_bias
                ssao_prog["u_kernel_size"] = self.ssao_kernel_size
                # Bind the SSAO kernel samples.  ModernGL exposes uniform
                # ARRAYS as a single member named "u_samples" (the per-element
                # "u_samples[0]" keys don't exist), so upload the whole array
                # in one go; fall back to per-element writes if a version
                # ever exposes indexed members instead.
                try:
                    ssao_prog["u_samples"].value = [
                        tuple(s) for s in self._ssao_kernel]
                except (KeyError, AttributeError):
                    for i, s in enumerate(self._ssao_kernel):
                        try:
                            ssao_prog[f"u_samples[{i}]"].value = tuple(s)
                        except (KeyError, IndexError):
                            break
                self._quad.render(ssao_prog)
                # Blur the raw AO to remove kernel speckle.
                ssao_blur_prog = ShaderLib.get(self.ctx, "ssao_blur")
                if ssao_blur_prog:
                    self.ssao_blur_fbo.clear(1.0, 1.0, 1.0, 1.0)
                    self.ssao_blur_fbo.use()
                    self.ssao_color.use(location=0)
                    ssao_blur_prog["u_tex"] = 0
                    self._quad.render(ssao_blur_prog)
                    ssao_result = self.ssao_blur_tex
                else:
                    ssao_result = self.ssao_color

        # =========================================================
        # 4) LIGHTING PASS  (fullscreen quad reads G-buffer; sun GGX +
        #    shadow + IBL ambient modulated by SSAO/baked AO)
        # =========================================================
        light_prog = ShaderLib.get(self.ctx, "lighting")
        self.scene_fbo.clear()
        self.scene_fbo.use()
        # Fullscreen passes don't use depth
        self.ctx.disable(moderngl.DEPTH_TEST)
        if light_prog:
            self.g_position.use(location=0)
            light_prog["g_position"] = 0
            self.g_normal.use(location=1)
            light_prog["g_normal"] = 1
            self.g_albedo.use(location=2)
            light_prog["g_albedo"] = 2
            self.g_material.use(location=3)
            light_prog["g_material"] = 3
            self.g_emissive.use(location=7)
            light_prog["g_emissive"] = 7
            self.shadow_map.use(location=4)
            light_prog["u_shadow_map"] = 4
            ssao_result.use(location=5)
            light_prog["u_ssao"] = 5
            self._env_tex.use(location=6)
            light_prog["u_env"] = 6
            if scene.sun:
                light_prog["u_sun_dir"].write(scene.sun.direction.to_bytes())
                light_prog["u_sun_color"].write(scene.sun.color.to_bytes())
                light_prog["u_sun_intensity"] = scene.sun.intensity
            else:
                sd = glm.vec3(0, -1, 0); sc = glm.vec3(0); si = 0.0
                light_prog["u_sun_dir"].write(sd.to_bytes())
                light_prog["u_sun_color"].write(sc.to_bytes())
                light_prog["u_sun_intensity"] = si
            amb_c = glm.vec3(0.25, 0.27, 0.32)
            light_prog["u_ambient_color"].write(amb_c.to_bytes())
            light_prog["u_ambient_intensity"] = 0.6
            # [TRIPLEA:render_stash_cluster_camera]
            # Stash camera matrices for the clustered-lighting grid build
            # inside _upload_local_lights (which only receives prog + scene).
            self._cl_view = view
            self._cl_proj = proj
            self._cl_near = float(getattr(camera, "near", 0.1))
            self._cl_far = float(getattr(camera, "far", 2000.0))
            self._upload_local_lights(light_prog, scene)
            light_prog["u_ibl_intensity"] = self.ibl_intensity
            light_prog["u_ao_direct"] = self.ao_direct
            light_prog["u_camera_pos"].write(camera.position.to_bytes())
            light_prog["u_light_space"].write(self._light_vp.to_bytes())
            # When shadows are off the map is never re-rendered — the shader
            # must skip sampling it instead of reading stale/identity data.
            light_prog["u_use_shadow"] = 1 if (self.enable_shadows and scene.sun) else 0
            light_prog["u_inv_proj"].write(glm.inverse(proj).to_bytes())
            light_prog["u_inv_view"].write(glm.inverse(view).to_bytes())
            # ---- procedural clouds (default scene feature, like the Sun) ----
            light_prog["u_time"] = float(time.monotonic() % 3600.0)
            clouds = scene.clouds
            if clouds is not None and clouds.enabled:
                light_prog["u_clouds_enabled"] = 1
                light_prog["u_cloud_coverage"] = float(clouds.coverage)
                light_prog["u_cloud_density"] = float(clouds.density)
                light_prog["u_cloud_scale"] = float(clouds.scale)
                light_prog["u_cloud_altitude"] = float(clouds.altitude)
                light_prog["u_cloud_speed"] = float(clouds.speed)
                light_prog["u_cloud_tint"].write(
                    glm.vec3(*clouds.tint).to_bytes())
                light_prog["u_cloud_wind"] = (float(clouds.wind_dir[0]),
                                              float(clouds.wind_dir[1]))
            else:
                light_prog["u_clouds_enabled"] = 0
            self._quad.render(light_prog)

        # =========================================================
        # =========================================================
        # [TRIPLEA:ocean_render_pass]
        # 4a-W) TRIPLE-A OCEAN  (analytic 3-octave Gerstner water plane,
        #       ray-marched per-pixel against the wave field and
        #       depth-composited with the G-buffer: Beer-Lambert body
        #       colour, fresnel sky reflection + sun glint, shoreline /
        #       crest / ripple foam, underwater fog.  Rendered into a
        #       ping texture and blitted back over the HDR scene because
        #       scene_color cannot be sampled while scene_fbo is bound.)
        # =========================================================
        try:
            _ocn = getattr(scene, "ocean", None)
            # Feed the CPU-side buoyancy / ripple state every frame.
            _OCEAN_STATE["enabled"] = bool(_ocn is not None and _ocn.enabled)
            if _ocn is not None:
                _OCEAN_STATE.update(
                    sea_level=float(_ocn.sea_level),
                    wave_height=float(_ocn.wave_height),
                    wave_speed=float(_ocn.wave_speed),
                    wind_dir=(float(_ocn.wind_dir[0]),
                              float(_ocn.wind_dir[1])),
                    wind_strength=float(_ocn.wind_strength),
                    choppiness=float(_ocn.choppiness),
                    buoyancy_enabled=bool(_ocn.buoyancy_enabled),
                    buoyancy_strength=float(_ocn.buoyancy_strength),
                    time=float(time.monotonic() % 3600.0))
            if (_ocn is not None and _ocn.enabled
                    and not getattr(self, "_ocean_pass_dead", False)):
                _oprog = ShaderLib.get(self.ctx, "ocean")
                _oblit = ShaderLib.get(self.ctx, "blit")
                if _oprog is not None and _oblit is not None:
                    # Lazily (re)build the full-res ping target — resize-safe.
                    if getattr(self, "_triplea_ping_size", None) != (self.w, self.h):
                        for _attr in ("_triplea_ping_fbo", "_triplea_ping"):
                            _old = getattr(self, _attr, None)
                            if _old is not None:
                                try:
                                    _old.release()
                                except Exception:
                                    pass
                        self._triplea_ping = self.ctx.texture(
                            (self.w, self.h), 3, dtype='f2')
                        self._triplea_ping.filter = (moderngl.LINEAR,
                                                     moderngl.LINEAR)
                        self._triplea_ping_fbo = self.ctx.framebuffer(
                            [self._triplea_ping])
                        self._triplea_ping_size = (self.w, self.h)
                    self._triplea_ping_fbo.use()
                    # Fullscreen passes don't use depth.
                    self.ctx.disable(moderngl.DEPTH_TEST)
                    self.g_position.use(location=0)
                    _oprog["g_position"] = 0
                    self.scene_color.use(location=1)
                    _oprog["u_scene"] = 1
                    self._env_tex.use(location=2)
                    _oprog["u_env"] = 2
                    _oprog["u_inv_proj"].write(glm.inverse(proj).to_bytes())
                    _oprog["u_inv_view"].write(glm.inverse(view).to_bytes())
                    _oprog["u_camera_pos"].write(camera.position.to_bytes())
                    if scene.sun:
                        _oprog["u_sun_dir"].write(scene.sun.direction.to_bytes())
                        _oprog["u_sun_color"].write(scene.sun.color.to_bytes())
                        _oprog["u_sun_intensity"] = float(scene.sun.intensity)
                    else:
                        _osd = glm.vec3(0, -1, 0)
                        _osc = glm.vec3(0)
                        _oprog["u_sun_dir"].write(_osd.to_bytes())
                        _oprog["u_sun_color"].write(_osc.to_bytes())
                        _oprog["u_sun_intensity"] = 0.0
                    _oprog["u_time"] = float(time.monotonic() % 3600.0)
                    _oprog["u_sea_level"] = float(_ocn.sea_level)
                    _oprog["u_water_shallow"].write(
                        glm.vec3(*_ocn.water_shallow).to_bytes())
                    _oprog["u_water_deep"].write(
                        glm.vec3(*_ocn.water_deep).to_bytes())
                    _oprog["u_wave_height"] = float(_ocn.wave_height)
                    _oprog["u_wave_speed"] = float(_ocn.wave_speed)
                    _oprog["u_wind_dir"] = (float(_ocn.wind_dir[0]),
                                            float(_ocn.wind_dir[1]))
                    _oprog["u_wind_strength"] = float(_ocn.wind_strength)
                    _oprog["u_choppiness"] = float(_ocn.choppiness)
                    _oprog["u_foam_amount"] = float(_ocn.foam_amount)
                    # Up to 8 expanding ring ripples from floating objects.
                    _rip = (_OCEAN_STATE.get("ripples") or [])[:8]
                    _rv = [(0.0, 0.0, -1000.0, 0.0)] * 8
                    for _ri, _rr in enumerate(_rip):
                        _rv[_ri] = (float(_rr[0]), float(_rr[1]),
                                    float(_rr[2]), float(_rr[3]))
                    try:
                        _oprog["u_ripples"].value = _rv
                    except (KeyError, ValueError):
                        pass
                    _oprog["u_ripple_count"] = len(_rip)
                    # The same cloud deck as the sky, so the sea reflects it.
                    _cl = getattr(scene, "clouds", None)
                    if _cl is not None and _cl.enabled:
                        _oprog["u_clouds_enabled"] = 1
                        _oprog["u_cloud_coverage"] = float(_cl.coverage)
                        _oprog["u_cloud_density"] = float(_cl.density)
                        _oprog["u_cloud_scale"] = float(_cl.scale)
                        _oprog["u_cloud_altitude"] = float(_cl.altitude)
                        _oprog["u_cloud_speed"] = float(_cl.speed)
                        _oprog["u_cloud_tint"].write(glm.vec3(*_cl.tint).to_bytes())
                        _oprog["u_cloud_wind"] = (float(_cl.wind_dir[0]), float(_cl.wind_dir[1]))
                    else:
                        _oprog["u_clouds_enabled"] = 0
                    self._quad.render(_oprog)
                    # Blit the composited ocean frame back onto the HDR scene.
                    self.scene_fbo.use()
                    self._triplea_ping.use(location=0)
                    _oblit["u_tex"] = 0
                    self._quad.render(_oblit)
        except Exception as _oex:
            if not getattr(self, "_ocean_pass_dead", False):
                Log.warn(f"Ocean pass disabled after error: {_oex}")
            self._ocean_pass_dead = True
        # =========================================================
        # [TRIPLEA:ssr_render_pass]
        # 4a-R) SCREEN-SPACE REFLECTIONS  (short-radius view-space ray
        #       march against the G-buffer position field — there is no
        #       sampleable depth texture, g_depth is a renderbuffer.
        #       24 steps + 4 binary refinements, 8 m max distance, 0.3 m
        #       thickness, screen-edge / distance fade, roughness skip,
        #       fresnel + metallic weighting.  Half-res into a lazily
        #       created target, then composited additively onto the HDR
        #       scene via the shared ping texture.)
        # =========================================================
        try:
            if (getattr(self, "enable_ssr", True)
                    and not getattr(self, "_ssr_pass_dead", False)):
                _sprog = ShaderLib.get(self.ctx, "ssr")
                _scprog = ShaderLib.get(self.ctx, "ssr_composite")
                _sblit = ShaderLib.get(self.ctx, "blit")
                if (_sprog is not None and _scprog is not None
                        and _sblit is not None):
                    # Lazily (re)build the half-res SSR target — resize-safe.
                    _hw, _hh = max(1, self.w // 2), max(1, self.h // 2)
                    if getattr(self, "_ssr_size", None) != (_hw, _hh):
                        for _attr in ("_ssr_fbo", "_ssr_tex"):
                            _old = getattr(self, _attr, None)
                            if _old is not None:
                                try:
                                    _old.release()
                                except Exception:
                                    pass
                        self._ssr_tex = self.ctx.texture((_hw, _hh), 3,
                                                         dtype='f2')
                        self._ssr_tex.filter = (moderngl.LINEAR,
                                                moderngl.LINEAR)
                        self._ssr_fbo = self.ctx.framebuffer([self._ssr_tex])
                        self._ssr_size = (_hw, _hh)
                    # Shared full-res ping (may already exist from the ocean).
                    if getattr(self, "_triplea_ping_size", None) != (self.w, self.h):
                        for _attr in ("_triplea_ping_fbo", "_triplea_ping"):
                            _old = getattr(self, _attr, None)
                            if _old is not None:
                                try:
                                    _old.release()
                                except Exception:
                                    pass
                        self._triplea_ping = self.ctx.texture(
                            (self.w, self.h), 3, dtype='f2')
                        self._triplea_ping.filter = (moderngl.LINEAR,
                                                     moderngl.LINEAR)
                        self._triplea_ping_fbo = self.ctx.framebuffer(
                            [self._triplea_ping])
                        self._triplea_ping_size = (self.w, self.h)
                    # Pass 1: ray-marched reflections → half-res target.
                    self._ssr_fbo.use()
                    # Fullscreen passes don't use depth.
                    self.ctx.disable(moderngl.DEPTH_TEST)
                    self.g_position.use(location=0)
                    _sprog["g_position"] = 0
                    self.g_normal.use(location=1)
                    _sprog["g_normal"] = 1
                    self.g_albedo.use(location=2)
                    _sprog["g_albedo"] = 2
                    self.g_material.use(location=3)
                    _sprog["g_material"] = 3
                    self.scene_color.use(location=4)
                    _sprog["u_scene"] = 4
                    _sprog["u_view"].write(view.to_bytes())
                    _sprog["u_proj"].write(proj.to_bytes())
                    _sprog["u_camera_pos"].write(camera.position.to_bytes())
                    self._quad.render(_sprog)
                    # Pass 2: scene + reflections → ping, blit ping back.
                    self._triplea_ping_fbo.use()
                    self.scene_color.use(location=0)
                    _scprog["u_scene"] = 0
                    self._ssr_tex.use(location=1)
                    _scprog["u_ssr"] = 1
                    self._quad.render(_scprog)
                    self.scene_fbo.use()
                    self._triplea_ping.use(location=0)
                    _sblit["u_tex"] = 0
                    self._quad.render(_sblit)
        except Exception as _sex:
            if not getattr(self, "_ssr_pass_dead", False):
                Log.warn(f"SSR disabled after error: {_sex}")
            self._ssr_pass_dead = True
        # 4b) FORWARD TRANSPARENCY  (glTF BLEND materials — real alpha
        #     blending over the lit scene, back-to-front, depth-tested
        #     against the opaque G-buffer depth.  Runs BEFORE bloom so
        #     glowing glass blooms like everything else.)
        # =========================================================
        self._render_forward_transparent(scene, camera, view, proj)

        # =========================================================
        # 5) BLOOM  (soft-knee bright → half-res blur + quarter-res blur
        #            → two-octave composite)
        # =========================================================
        if self.enable_bloom:
            bright_prog = ShaderLib.get(self.ctx, "bright")
            if bright_prog:
                self.bloom_bright_fbo.clear()
                self.bloom_bright_fbo.use()
                self.scene_color.use(location=0)
                bright_prog["u_tex"] = 0
                bright_prog["u_threshold"] = self.bloom_threshold
                bright_prog["u_knee"] = self.bloom_knee
                self._quad.render(bright_prog)
            blur_prog = ShaderLib.get(self.ctx, "blur")
            if blur_prog:
                # Octave 1 (half res): H then V
                self.bloom_ping_fbo.clear(); self.bloom_ping_fbo.use()
                self.bloom_bright.use(location=0)
                blur_prog["u_tex"] = 0
                blur_prog["u_direction"] = (1.0, 0.0)
                self._quad.render(blur_prog)
                self.bloom_pong_fbo.clear(); self.bloom_pong_fbo.use()
                self.bloom_ping.use(location=0)
                blur_prog["u_tex"] = 0
                blur_prog["u_direction"] = (0.0, 1.0)
                self._quad.render(blur_prog)
                # Octave 2 (quarter res): downsample octave-1, H then V —
                # the wider, softer halo.
                self.bloom_ping2_fbo.clear(); self.bloom_ping2_fbo.use()
                self.bloom_pong.use(location=0)
                blur_prog["u_tex"] = 0
                blur_prog["u_direction"] = (1.0, 0.0)
                self._quad.render(blur_prog)
                self.bloom_pong2_fbo.clear(); self.bloom_pong2_fbo.use()
                self.bloom_ping2.use(location=0)
                blur_prog["u_tex"] = 0
                blur_prog["u_direction"] = (0.0, 1.0)
                self._quad.render(blur_prog)
                self._bloom_final = self.bloom_pong
                self._bloom_final2 = self.bloom_pong2
            else:
                self._bloom_final = None
                self._bloom_final2 = None
        else:
            self._bloom_final = None
            self._bloom_final2 = None

        # =========================================================
        # 6) COMPOSITE + TONE MAP  → final LDR framebuffer
        # =========================================================
        comp_prog = ShaderLib.get(self.ctx, "composite")
        if comp_prog:
            self.final_fbo.clear()
            self.final_fbo.use()
            self.scene_color.use(location=0)
            comp_prog["u_scene"] = 0
            if self._bloom_final is not None:
                self._bloom_final.use(location=1)
                comp_prog["u_bloom"] = 1
            else:
                self.scene_color.use(location=1)
                comp_prog["u_bloom"] = 1
            if self._bloom_final2 is not None:
                self._bloom_final2.use(location=2)
                comp_prog["u_bloom2"] = 2
            else:
                self.scene_color.use(location=2)
                comp_prog["u_bloom2"] = 2
            comp_prog["u_bloom_strength"] = self.bloom_strength if self.enable_bloom else 0.0
            comp_prog["u_exposure"] = self.exposure
            self._quad.render(comp_prog)

        # =========================================================
        # 7) FXAA  → default framebuffer (the window)
        # =========================================================
        fxaa_prog = ShaderLib.get(self.ctx, "fxaa") if self.enable_fxaa else None
        self.ctx.screen.use()
        if fxaa_prog:
            self.final_color.use(location=0)
            fxaa_prog["u_tex"] = 0
            fxaa_prog["u_inv_size"] = (1.0 / max(1, self.w), 1.0 / max(1, self.h))
            self._quad.render(fxaa_prog)
        else:
            # FXAA off: raw 1:1 blit of final_color (already tonemapped —
            # do NOT run it through composite's ACES/gamma a second time)
            fallback = ShaderLib.get(self.ctx, "blit")
            if fallback:
                self.final_color.use(location=0)
                fallback["u_tex"] = 0
                self._quad.render(fallback)

    def _upload_local_lights(self, prog, scene: Scene):
        """Upload all point/spot lights to the deferred lighting shader.

        The shader keeps fixed-size arrays (16 of each), which is fast and
        GLSL 3.30-safe.  Lights beyond that are ignored with a warning rather
        than silently dropping them."""
        max_lights = 16
        point_nodes = scene.traverse_point_lights()
        spot_nodes = scene.traverse_spot_lights()
        if len(point_nodes) > max_lights or len(spot_nodes) > max_lights:
            Log.warn(f"Local light limit is {max_lights} per type; extra "
                     f"lights are ignored this frame.")
        point_nodes = point_nodes[:max_lights]
        spot_nodes = spot_nodes[:max_lights]

        point_pos = [(0.0, 0.0, 0.0)] * max_lights
        point_color = [(0.0, 0.0, 0.0)] * max_lights
        point_intensity = [0.0] * max_lights
        point_range = [0.0] * max_lights
        for i, (node, light) in enumerate(point_nodes):
            p = node.world_position()
            point_pos[i] = (float(p.x), float(p.y), float(p.z))
            point_color[i] = (float(light.color.x), float(light.color.y),
                              float(light.color.z))
            point_intensity[i] = max(0.0, float(light.intensity))
            point_range[i] = max(0.01, float(light.range))

        spot_pos = [(0.0, 0.0, 0.0)] * max_lights
        spot_dir = [(0.0, 0.0, -1.0)] * max_lights
        spot_color = [(0.0, 0.0, 0.0)] * max_lights
        spot_intensity = [0.0] * max_lights
        spot_range = [0.0] * max_lights
        spot_inner_cos = [1.0] * max_lights
        spot_outer_cos = [1.0] * max_lights
        for i, (node, light) in enumerate(spot_nodes):
            p = node.world_position()
            d = Scene.forward_from_rotation(node.rotation_euler)
            inner = _clamp(float(light.inner_angle), 0.1, 179.0)
            outer = _clamp(float(light.outer_angle), inner, 179.0)
            spot_pos[i] = (float(p.x), float(p.y), float(p.z))
            spot_dir[i] = (float(d.x), float(d.y), float(d.z))
            spot_color[i] = (float(light.color.x), float(light.color.y),
                             float(light.color.z))
            spot_intensity[i] = max(0.0, float(light.intensity))
            spot_range[i] = max(0.01, float(light.range))
            # Unity exposes full cone angles; the shader compares dot products,
            # so store cos(half-angle).
            spot_inner_cos[i] = math.cos(math.radians(inner * 0.5))
            spot_outer_cos[i] = math.cos(math.radians(outer * 0.5))

        uniforms = {
            "u_point_count": len(point_nodes),
            "u_point_pos": point_pos,
            "u_point_color": point_color,
            "u_point_intensity": point_intensity,
            "u_point_range": point_range,
            "u_spot_count": len(spot_nodes),
            "u_spot_pos": spot_pos,
            "u_spot_dir": spot_dir,
            "u_spot_color": spot_color,
            "u_spot_intensity": spot_intensity,
            "u_spot_range": spot_range,
            "u_spot_inner_cos": spot_inner_cos,
            "u_spot_outer_cos": spot_outer_cos,
        }
        for name, value in uniforms.items():
            try:
                prog[name].value = value
            except (KeyError, AttributeError, ValueError) as ex:
                Log.warn(f"Could not upload local-light uniform {name}: {ex}")

        # [TRIPLEA:upload_local_lights_clustered]
        # ---- Clustered lighting grid build ------------------------------
        # Screen is divided into 16x16 px tiles x 16 exponential view-space
        # depth slices.  Each cluster stores a 32-bit light bitmask
        # (bits 0..15 = point lights, bits 16..31 = spot lights) in an
        # R32UI texture read with texelFetch in the lighting shader.
        # ANY failure leaves u_clustered_enabled = 0 so the shader runs
        # the original full loops — the scene stays lit no matter what.
        clustered_ok = 0
        try:
            cl_view = getattr(self, "_cl_view", None)
            cl_proj = getattr(self, "_cl_proj", None)
            if cl_view is not None and cl_proj is not None:
                near = max(0.01, float(getattr(self, "_cl_near", 0.1)))
                far = max(near * 2.0, float(getattr(self, "_cl_far", 2000.0)))
                slices = 16
                tiles_x = max(1, (int(self.w) + 15) // 16)
                tiles_y = max(1, (int(self.h) + 15) // 16)
                # Lazily (re)create the grid texture when the tile
                # resolution changes (resize-safe; _build_framebuffers is
                # not involved).
                grid_key = (tiles_x, tiles_y)
                if (getattr(self, "_cl_grid_key", None) != grid_key
                        or getattr(self, "_cl_grid_tex", None) is None):
                    old_tex = getattr(self, "_cl_grid_tex", None)
                    if old_tex is not None:
                        try:
                            old_tex.release()
                        except Exception:
                            pass
                    # R32UI: one uint bitmask per (tile, slice) texel.
                    self._cl_grid_tex = self.ctx.texture(
                        (tiles_x, tiles_y * slices), 1, dtype="u4")
                    # Integer textures are only complete with NEAREST.
                    self._cl_grid_tex.filter = (moderngl.NEAREST,
                                                moderngl.NEAREST)
                    self._cl_grid_key = grid_key
                grid = np.zeros((tiles_y * slices, tiles_x),
                                dtype=np.uint32)
                vm = np.array([[cl_view[c][r] for c in range(4)]
                               for r in range(4)], dtype=np.float64)
                pm = np.array([[cl_proj[c][r] for c in range(4)]
                               for r in range(4)], dtype=np.float64)
                log_ratio = math.log(far / near)

                def _slice_of(depth):
                    # Exponential depth slicing — must match the shader.
                    d = min(max(depth, near), far)
                    s = int(math.log(d / near) / log_ratio * slices)
                    return min(max(s, 0), slices - 1)

                def _stamp_cluster(wx, wy, wz, rng, bit):
                    """Conservatively stamp `bit` into every cluster the
                    light sphere may touch.  Screen extents use the exact
                    tangent angles of the projected sphere (per axis the
                    NDC extent depends only on the 2D disk the ball casts
                    on that axis' plane); the depth range is padded ~5% so
                    fp32 shader math can never disagree at a slice edge."""
                    vp = vm @ np.array([wx, wy, wz, 1.0],
                                       dtype=np.float64)
                    cx, cy, cz = float(vp[0]), float(vp[1]), float(vp[2])
                    depth = -cz
                    if depth + rng < near or depth - rng > far:
                        return  # sphere cannot reach any visible fragment
                    pad = rng * 0.05 + 0.02
                    s0 = _slice_of(depth - rng - pad)
                    s1 = _slice_of(depth + rng + pad)
                    if cz + rng >= -1e-6:
                        # Sphere crosses the eye plane — projection is
                        # meaningless; cover every tile on these slices.
                        tx0, tx1 = 0, tiles_x - 1
                        ty0, ty1 = 0, tiles_y - 1
                    else:
                        def _axis_bounds(cc):
                            # Tangent angles from the eye to the sphere's
                            # 2D disk → exact NDC extent for this axis.
                            rho = math.hypot(cc, cz)
                            if rho <= rng:
                                return None  # eye inside disk → full range
                            phi = math.atan2(cc, -cz)
                            alpha = math.asin(min(1.0, rng / rho))
                            lim = math.pi * 0.5 - 1e-4
                            return (math.tan(max(phi - alpha, -lim)),
                                    math.tan(min(phi + alpha, lim)))
                        xb = _axis_bounds(cx)
                        yb = _axis_bounds(cy)
                        ndc_x0, ndc_x1 = ((-1.0, 1.0) if xb is None
                                          else (float(pm[0][0]) * xb[0],
                                                float(pm[0][0]) * xb[1]))
                        ndc_y0, ndc_y1 = ((-1.0, 1.0) if yb is None
                                          else (float(pm[1][1]) * yb[0],
                                                float(pm[1][1]) * yb[1]))
                        if (ndc_x0 > 1.0 or ndc_x1 < -1.0
                                or ndc_y0 > 1.0 or ndc_y1 < -1.0):
                            return  # fully off-screen
                        tx0 = int(math.floor(
                            (ndc_x0 * 0.5 + 0.5) * tiles_x))
                        tx1 = int(math.floor(
                            (ndc_x1 * 0.5 + 0.5) * tiles_x))
                        ty0 = int(math.floor(
                            (ndc_y0 * 0.5 + 0.5) * tiles_y))
                        ty1 = int(math.floor(
                            (ndc_y1 * 0.5 + 0.5) * tiles_y))
                        tx0 = min(max(tx0, 0), tiles_x - 1)
                        tx1 = min(max(tx1, 0), tiles_x - 1)
                        ty0 = min(max(ty0, 0), tiles_y - 1)
                        ty1 = min(max(ty1, 0), tiles_y - 1)
                    rows = []
                    for ty in range(ty0, ty1 + 1):
                        rows.extend(range(ty * slices + s0,
                                          ty * slices + s1 + 1))
                    grid[np.ix_(np.array(rows, dtype=np.intp),
                                np.arange(tx0, tx1 + 1))] |= bit

                for i, (node, light) in enumerate(point_nodes):
                    p = node.world_position()
                    _stamp_cluster(float(p.x), float(p.y), float(p.z),
                                   max(0.01, float(light.range)),
                                   np.uint32(1 << i))
                for i, (node, light) in enumerate(spot_nodes):
                    # Conservative sphere bound for the cone — the shader's
                    # cone test still rejects fragments outside the cone.
                    p = node.world_position()
                    _stamp_cluster(float(p.x), float(p.y), float(p.z),
                                   max(0.01, float(light.range)),
                                   np.uint32(1 << (16 + i)))
                self._cl_grid_tex.write(grid.tobytes())
                self._cl_grid_tex.use(location=8)
                prog["u_cluster_mask"] = 8
                prog["u_cluster_dims"] = (tiles_x, tiles_y)
                prog["u_view"].write(cl_view.to_bytes())
                prog["u_near"] = float(near)
                prog["u_far"] = float(far)
                clustered_ok = 1
        except Exception as ex:
            clustered_ok = 0
            try:
                Log.warn(f"Clustered lighting grid build failed; "
                         f"falling back to the full light loop: {ex}")
            except Exception:
                pass
        try:
            prog["u_clustered_enabled"] = clustered_ok
        except (KeyError, AttributeError, ValueError):
            pass

    # ------------------------------------------- forward transparency
    def _render_forward_transparent(self, scene: Scene, camera: Camera,
                                    view: glm.mat4, proj: glm.mat4):
        """Draw every BLEND-material mesh with true alpha blending.

        Deferred G-buffers hold exactly one surface per pixel, so blended
        transparency is impossible inside the lighting pass.  The standard
        hybrid solution (Unity HDRP / UE / Frostbite all do this): opaque +
        cut-out go deferred, BLEND goes forward afterwards.  We render into
        scene_fbo_depth — the HDR scene colour with the G-buffer's depth
        attached — so transparent surfaces depth-test against opaque
        geometry.  Depth WRITES stay off and meshes draw far→near
        (painter's algorithm) so layered glass sorts correctly."""
        prog = ShaderLib.get(self.ctx, "forward")
        if prog is None:
            return
        items = []
        for node, mesh, model in scene.traverse_meshes():
            mat = node.material
            if mat is None:
                continue
            if (mat.alpha_mode or "OPAQUE").upper() != "BLEND":
                continue
            items.append((mesh, model, mat))
        if not items:
            return

        cam = camera.position
        # Painter's sort: farthest first.  Origin of the model matrix is a
        # good enough key at the object granularity this engine works at.
        def _dist2(it):
            p = glm.vec3(it[1] * glm.vec4(0.0, 0.0, 0.0, 1.0)) - cam
            return p.x * p.x + p.y * p.y + p.z * p.z
        items.sort(key=_dist2, reverse=True)

        self.scene_fbo_depth.use()
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.depth_func = '<='
        self.ctx.depth_mask = False     # test against depth, never write it
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA,
                               moderngl.ONE_MINUS_SRC_ALPHA)

        prog["u_view"].write(view.to_bytes())
        prog["u_proj"].write(proj.to_bytes())
        prog["u_camera_pos"].write(cam.to_bytes())
        if scene.sun:
            prog["u_sun_dir"].write(scene.sun.direction.to_bytes())
            prog["u_sun_color"].write(scene.sun.color.to_bytes())
            prog["u_sun_intensity"] = scene.sun.intensity
        else:
            prog["u_sun_dir"].write(glm.vec3(0, -1, 0).to_bytes())
            prog["u_sun_color"].write(glm.vec3(0).to_bytes())
            prog["u_sun_intensity"] = 0.0
        prog["u_ambient_color"].write(glm.vec3(0.25, 0.27, 0.32).to_bytes())
        prog["u_ambient_intensity"] = 0.6
        prog["u_ibl_intensity"] = self.ibl_intensity
        self._env_tex.use(location=6)
        prog["u_env"] = 6

        for mesh, model, mat in items:
            self._bind_forward_material(prog, mat)
            prog["u_model"].write(model.to_bytes())
            vao = mesh.get_vao(prog)
            if mesh.indexed:
                vao.render(moderngl.TRIANGLES)
            else:
                vao.render(moderngl.TRIANGLES, vertices=mesh.vertex_count)

        # Restore the state the post chain expects.
        self.ctx.depth_mask = True
        self.ctx.disable(moderngl.BLEND)
        self.ctx.disable(moderngl.DEPTH_TEST)

    def _bind_forward_material(self, prog, mat: Material):
        """Bind the subset of material state the forward shader declares
        (setting a uniform a program doesn't have raises KeyError, so this
        intentionally does NOT reuse _bind_material)."""
        prog["u_base_color"] = mat.base_color
        prog["u_metallic"] = mat.metallic
        prog["u_roughness"] = mat.roughness
        prog["u_emissive"] = mat.emissive
        prog["u_emissive_intensity"] = mat.emissive_intensity
        prog["u_alpha_cutoff"] = float(getattr(mat, "alpha_cutoff", 0.5))
        prog["u_double_sided"] = 1 if mat.double_sided else 0
        albedo_tex = self.get_texture(mat.base_color_tex) if mat.base_color_tex else self._default_tex
        albedo_tex.use(location=5)
        prog["u_albedo_tex"] = 5
        prog["u_has_albedo"] = 1 if mat.base_color_tex else 0
        mr_tex = self.get_texture(mat.metallic_roughness_tex) if mat.metallic_roughness_tex else self._default_tex
        mr_tex.use(location=10)
        prog["u_mr_tex"] = 10
        prog["u_has_mr"] = 1 if mat.metallic_roughness_tex else 0
        nrm_tex = self.get_texture(mat.normal_tex) if mat.normal_tex else self._default_normal_tex
        nrm_tex.use(location=11)
        prog["u_normal_tex"] = 11
        prog["u_has_normal"] = 1 if mat.normal_tex else 0
        emi_tex = self.get_texture(mat.emissive_tex) if mat.emissive_tex else self._default_tex
        emi_tex.use(location=12)
        prog["u_emissive_tex"] = 12
        prog["u_has_emissive"] = 1 if mat.emissive_tex else 0

    def _bind_material(self, prog, mat: Material):
        prog["u_base_color"] = mat.base_color
        prog["u_metallic"] = mat.metallic
        prog["u_roughness"] = mat.roughness
        prog["u_emissive"] = mat.emissive
        prog["u_emissive_intensity"] = mat.emissive_intensity
        # ---- transparency (glTF alphaMode) ----
        # Map the material's alpha_mode string to the int the shader expects:
        #   OPAQUE → 0, MASK → 1, BLEND → 2.
        am = (mat.alpha_mode or "OPAQUE").upper()
        alpha_mode_int = 1 if am == "MASK" else (2 if am == "BLEND" else 0)
        # Only request an alpha test if there's actually an albedo texture
        # to sample alpha from; without one the constant u_base_color.a is
        # used (still useful for solid-colour cut-outs).
        prog["u_alpha_mode"] = alpha_mode_int
        # Editable per material in the inspector (glTF alphaCutoff).
        prog["u_alpha_cutoff"] = float(getattr(mat, "alpha_cutoff", 0.5))
        # glTF doubleSided → two-sided lighting in the geometry shader.
        prog["u_double_sided"] = 1 if mat.double_sided else 0
        # albedo
        albedo_tex = self.get_texture(mat.base_color_tex) if mat.base_color_tex else self._default_tex
        albedo_tex.use(location=5)
        prog["u_albedo_tex"] = 5
        prog["u_has_albedo"] = 1 if mat.base_color_tex else 0
        # metallic-roughness
        mr_tex = self.get_texture(mat.metallic_roughness_tex) if mat.metallic_roughness_tex else self._default_tex
        mr_tex.use(location=6)
        prog["u_mr_tex"] = 6
        prog["u_has_mr"] = 1 if mat.metallic_roughness_tex else 0
        # normal
        nrm_tex = self.get_texture(mat.normal_tex) if mat.normal_tex else self._default_normal_tex
        nrm_tex.use(location=7)
        prog["u_normal_tex"] = 7
        prog["u_has_normal"] = 1 if mat.normal_tex else 0
        # emissive
        emi_tex = self.get_texture(mat.emissive_tex) if mat.emissive_tex else self._default_tex
        emi_tex.use(location=8)
        prog["u_emissive_tex"] = 8
        prog["u_has_emissive"] = 1 if mat.emissive_tex else 0
        # baked AO (glTF occlusionTexture)
        ao_tex = self.get_texture(mat.occlusion_tex) if mat.occlusion_tex else self._default_tex
        ao_tex.use(location=9)
        prog["u_ao_tex"] = 9
        prog["u_has_ao"] = 1 if mat.occlusion_tex else 0

    def release(self):
        for fbo in [self.g_fbo, getattr(self, "g_fbo_msaa", None),
                    self.scene_fbo, getattr(self, "scene_fbo_depth", None),
                    self.ssao_fbo, self.ssao_blur_fbo,
                    self.bloom_bright_fbo, self.bloom_ping_fbo,
                    self.bloom_pong_fbo, self.bloom_ping2_fbo,
                    self.bloom_pong2_fbo, self.final_fbo, self.shadow_fbo]:
            try:
                if fbo is not None: fbo.release()
            except Exception: pass
        for tex in [self.g_position, self.g_normal, self.g_albedo, self.g_material,
                    getattr(self, "g_emissive", None),
                    self.scene_color, self.ssao_color, self.ssao_blur_tex,
                    self.bloom_bright,
                    self.bloom_ping, self.bloom_pong,
                    self.bloom_ping2, self.bloom_pong2, self.final_color,
                    self.shadow_map, self._default_tex, self._default_normal_tex,
                    self._noise_tex, self._env_tex]:
            try: tex.release()
            except Exception: pass
        for rb in ([self.g_depth, self.scene_depth,
                    getattr(self, "g_msaa_depth_rb", None)]
                   + list(getattr(self, "g_msaa_color_rbs", []) or [])):
            try:
                if rb is not None: rb.release()
            except Exception: pass
        self._quad.release()


# @heaven3d-part world/terrain_editor.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# [TERRAINPATCH:gameguru_terrain_editor_v2]
# #############################################################################
#
#  GameGuru-style Terrain Editor + GPU grass — append-only patch module.
#
#  Installed by Terrainupdate.py.  Follows the engine's own patch idiom
#  (capture-previous + wrap + reassign, double-install guard, try/except
#  with one-shot logs and graceful fallbacks):
#
#    * Flat-start 3x3 km heightmap terrain (the old random noise generator
#      is retired: `_heightmap_noise` is rebound to a flat stub so BOTH the
#      mesh and the physics heightfield stay consistent everywhere).
#    * Sculpt brushes: Raise (mountains) / Dig (holes) / Smooth / Stamp
#      (plateau) / Ramp (drag A->B, snapshot preview).
#    * Paint brushes: Grass / Dirt / Rock / Asphalt / Sand, 5-way splat map,
#      per-layer PBR (albedo + normal) texture arrays, per-layer tiling
#      (real-world scale vs the 1.80 m player capsule), per-layer roughness,
#      and slope-angle AUTO-ROCK with smooth fade in/out (GameGuru-style).
#      Rock / asphalt / sand automatically remove the 3D grass; painting
#      grass brings it back; a dedicated erase brush removes grass.
#    * GPU-instanced, LOD-ringed, wind-swaying 3D grass that bends away from
#      the player / moving objects (pushers uploaded as uniforms).  Default
#      blade height 0.30 m, editable.  Drawn forward into scene_fbo_depth so
#      it is depth-correct, sun-lit, bloomed, tonemapped and FXAA'd with the
#      rest of the frame.
#    * Physics: terrain nodes get a HeightfieldCollider bound to the LIVE
#      heights array — collision always matches the sculpted render mesh, so
#      nothing ever falls through the terrain.
#    * Save/Load: heights / splat / grass mask + all params round-trip
#      through .heaven3d as zlib+base64 JSON strings.
#    * Editor: blue TERRAIN button (Heaven3D.py navbar), immediate-mode
#      panel, visible brush ring, '[' / ']' brush size, ESC to close.
#      Everything editor-only is mode-guarded; the standalone runtime only
#      carries rendering / grass / physics / IO.
#
#  Everything below is inert until an engine instance exists; names defined
#  earlier in the file are referenced directly, runtime-only names (ctx,
#  scene, engine) are only touched inside function bodies.
# #############################################################################

if not globals().get("_TERRAINPATCH_INSTALLED"):
    _TERRAINPATCH_INSTALLED = True
    import base64 as _tp_b64
    import zlib as _tp_zlib

    _TP_LOGGED = set()

    def _tp_log_once(key, msg):
        if key in _TP_LOGGED:
            return
        _TP_LOGGED.add(key)
        try:
            Log.warn(msg)
        except Exception:
            pass

    def _tp_info(msg):
        try:
            Log.info(msg)
        except Exception:
            print("[TERRAINPATCH] " + str(msg))

    # ------------------------------------------------------------------ #
    #  Small helpers                                                      #
    # ------------------------------------------------------------------ #
    def _tp_b64_encode(arr) -> str:
        """numpy array -> zlib-compressed base64 ascii string (JSON-safe)."""
        a = np.ascontiguousarray(arr)
        payload = _tp_zlib.compress(a.tobytes(), 6)
        return _tp_b64.b64encode(payload).decode("ascii")

    def _tp_b64_decode(s: str, dtype, shape):
        """Inverse of _tp_b64_encode; None on any mismatch (safe load)."""
        try:
            raw = _tp_zlib.decompress(_tp_b64.b64decode(s.encode("ascii")))
            a = np.frombuffer(raw, dtype=dtype)
            if int(np.prod(shape)) != a.size:
                return None
            return a.reshape(shape).copy()
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Tileable value-noise kit (pure numpy — no new dependencies)        #
    # ------------------------------------------------------------------ #
    def _tp_hash_lattice(ix, iz, period, seed):
        """Deterministic pseudo-random float in [0,1) per lattice point.
        `period` makes the lattice wrap -> the noise tiles perfectly."""
        ix = np.mod(ix, period).astype(np.int64)
        iz = np.mod(iz, period).astype(np.int64)
        h = (ix * 374761393 + iz * 668265263 + int(seed) * 1442695041)
        h = np.bitwise_xor(h, np.right_shift(h, 13)) * 1274126177
        h = np.bitwise_xor(h, np.right_shift(h, 16))
        return (h & 0x7FFFFFFF).astype(np.float64) / float(0x7FFFFFFF)

    def _tp_value_noise(size, freq, seed):
        """Tileable value noise, `freq` lattice cells across `size` px."""
        freq = max(1, int(freq))
        xs = np.arange(size, dtype=np.float64) * (freq / float(size))
        ix = np.floor(xs).astype(np.int64)
        fx = xs - ix
        fx = fx * fx * (3.0 - 2.0 * fx)          # smoothstep interpolation
        ix1 = ix + 1
        g00 = _tp_hash_lattice(ix[None, :], ix[:, None], freq, seed)
        g10 = _tp_hash_lattice(ix1[None, :], ix[:, None], freq, seed)
        g01 = _tp_hash_lattice(ix[None, :], ix1[:, None], freq, seed)
        g11 = _tp_hash_lattice(ix1[None, :], ix1[:, None], freq, seed)
        # gRC indexes [z, x]: g00 uses ix for both then we fix axes below
        top = g00 * (1.0 - fx[None, :]) + g10 * fx[None, :]
        bot = g01 * (1.0 - fx[None, :]) + g11 * fx[None, :]
        img = top * (1.0 - fx[:, None]) + bot * fx[:, None]
        return img.astype(np.float32)

    def _tp_fbm(size, base_freq, octaves, seed, gain=0.5, lacunarity=2.0):
        """Fractal Brownian motion over tileable value noise, normalised 0..1."""
        total = np.zeros((size, size), dtype=np.float32)
        amp, freq, norm = 1.0, float(base_freq), 0.0
        for o in range(max(1, int(octaves))):
            total += _tp_value_noise(size, max(1, int(round(freq))),
                                     seed + o * 131) * amp
            norm += amp
            amp *= gain
            freq *= lacunarity
        total /= max(norm, 1e-6)
        lo, hi = float(total.min()), float(total.max())
        if hi - lo > 1e-8:
            total = (total - lo) / (hi - lo)
        return total

    def _tp_ridged(size, base_freq, octaves, seed):
        """Ridged fbm (1-|2n-1|) — rocky strata / mountain detail."""
        total = np.zeros((size, size), dtype=np.float32)
        amp, freq, norm = 1.0, float(base_freq), 0.0
        for o in range(max(1, int(octaves))):
            n = _tp_value_noise(size, max(1, int(round(freq))), seed + o * 733)
            total += (1.0 - np.abs(2.0 * n - 1.0)) * amp
            norm += amp
            amp *= 0.5
            freq *= 2.1
        total /= max(norm, 1e-6)
        lo, hi = float(total.min()), float(total.max())
        if hi - lo > 1e-8:
            total = (total - lo) / (hi - lo)
        return total

    def _tp_normal_from_height(h, strength=2.0):
        """Height field -> tangent-space normal map (RGB uint8, Sobel)."""
        gy, gx = np.gradient(h.astype(np.float32))
        scale = strength * (h.shape[0] / 256.0)
        nx, ny, nz = -gx * scale, -gy * scale, np.ones_like(h)
        ln = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-8
        out = np.stack([nx / ln, ny / ln, nz / ln], axis=-1)
        return ((out * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8)

    # ------------------------------------------------------------------ #
    #  AAA procedural terrain layer textures (albedo sRGB + normal map)   #
    #  5 layers: 0 grass, 1 dirt, 2 rock, 3 asphalt, 4 sand.              #
    #  Authored fully procedurally — built-in, zero asset files.          #
    # ------------------------------------------------------------------ #
    _TP_LAYER_NAMES = ("grass", "dirt", "rock", "asphalt", "sand")
    # Tiles per metre (layer UV = terrain01_uv * size * tiling).  Tuned so
    # texture features read correctly next to the 1.80 m player capsule.
    _TP_LAYER_TILING = (0.55, 0.42, 0.26, 0.24, 0.34)
    _TP_LAYER_ROUGH = (0.86, 0.95, 0.78, 0.90, 0.96)
    _TP_TEX_SIZE = 512          # per layer; 512 keeps iGPUs fast, still crisp

    def _tp_build_layer_maps(layer: int, size: int = _TP_TEX_SIZE):
        """Return (albedo_rgba_u8, normal_rgb_u8) for one layer."""
        s = int(size)
        seed = 1000 + layer * 97
        f1 = _tp_fbm(s, 4, 5, seed + 1)              # large patchiness
        f2 = _tp_fbm(s, 12, 5, seed + 2)             # mid detail
        spk = _tp_value_noise(s, s // 2, seed + 3)   # pixel speckle
        warp = _tp_fbm(s, 3, 3, seed + 4)

        if layer == 0:      # ---- GRASS ----
            blade = _tp_value_noise(s, s // 3, seed + 5)      # blade stipple
            h = 0.60 * f2 + 0.40 * blade
            g = 0.30 + 0.24 * f1 + 0.05 * blade
            r = 0.13 + 0.13 * f1 + 0.03 * blade
            b = 0.08 + 0.08 * f1 + 0.015 * blade
            alb = np.stack([r, g, b], axis=-1)
            nrm = _tp_normal_from_height(h, 2.4)
        elif layer == 1:    # ---- DIRT ----
            clod = _tp_fbm(s, 24, 4, seed + 6)
            h = 0.5 * clod + 0.35 * f2 + 0.15 * spk
            r = 0.34 + 0.16 * f1 + 0.07 * clod
            g = 0.23 + 0.11 * f1 + 0.05 * clod
            b = 0.13 + 0.07 * f1 + 0.03 * clod
            stone = (spk > 0.965)
            r = np.where(stone, r + 0.10, r)
            g = np.where(stone, g + 0.10, g)
            b = np.where(stone, b + 0.09, b)
            alb = np.stack([r, g, b], axis=-1)
            nrm = _tp_normal_from_height(h, 2.0)
        elif layer == 2:    # ---- ROCK ----
            rid = _tp_ridged(s, 5, 5, seed + 7)
            crack = 1.0 - np.abs(2.0 * _tp_fbm(s, 9, 4, seed + 8) - 1.0)
            crack = np.clip((crack - 0.82) / 0.18, 0.0, 1.0)  # dark fissures
            h = 0.7 * rid + 0.3 * f2 - 0.35 * crack
            v = 0.34 + 0.22 * rid + 0.08 * f1 - 0.16 * crack
            r = v * 1.00
            g = v * 0.98
            b = v * 0.94
            alb = np.stack([r, g, b], axis=-1)
            nrm = _tp_normal_from_height(h, 3.2)
        elif layer == 3:    # ---- ASPHALT ----
            agg = (_tp_value_noise(s, s, seed + 9) > 0.80).astype(np.float32)
            wear = _tp_fbm(s, 2, 3, seed + 10)
            h = 0.25 * f2 + 0.5 * agg + 0.25 * spk
            v = 0.115 + 0.05 * f1 + 0.03 * f2 + 0.10 * agg
            v = v * (0.92 + 0.16 * wear)
            alb = np.stack([v, v, v * 1.04], axis=-1)
            nrm = _tp_normal_from_height(h, 1.4)
        else:               # ---- SAND ----
            xx = np.arange(s, dtype=np.float32)[None, :]
            ripple = np.sin((xx / s) * math.pi * 2 * 26.0
                            + warp * 9.0) * 0.5 + 0.5
            ripple = np.broadcast_to(ripple, (s, s)).copy()
            h = 0.45 * ripple + 0.35 * f2 + 0.2 * spk
            r = 0.74 + 0.12 * f1 + 0.06 * ripple
            g = 0.63 + 0.11 * f1 + 0.05 * ripple
            b = 0.42 + 0.08 * f1 + 0.03 * ripple
            alb = np.stack([r, g, b], axis=-1)
            nrm = _tp_normal_from_height(h, 1.7)

        alb = np.clip(alb, 0.0, 1.0)
        alpha = np.full((s, s, 1), 255, dtype=np.uint8)
        alb_u8 = (alb * 255.0).astype(np.uint8)
        return np.concatenate([alb_u8, alpha], axis=-1), nrm

    # ------------------------------------------------------------------ #
    #  Grass blade texture (RGBA cutout) — 3 blades on a 128px card       #
    # ------------------------------------------------------------------ #
    def _tp_build_grass_texture(size: int = 128):
        s = int(size)
        yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
        u = xx / (s - 1.0)
        v = yy / (s - 1.0)                       # v=0 top row of the array
        alpha = np.zeros((s, s), dtype=np.float32)
        col_r = np.zeros((s, s), dtype=np.float32)
        col_g = np.zeros((s, s), dtype=np.float32)
        col_b = np.zeros((s, s), dtype=np.float32)
        # 3 blades: centre + two leaning side blades (analytic shapes)
        for cx, lean, wd, hue in ((0.50, 0.00, 0.052, 0.00),
                                  (0.30, -0.16, 0.044, 0.06),
                                  (0.70, 0.16, 0.044, -0.05)):
            t = 1.0 - v                          # 0 at bottom, 1 at tip
            centre = cx + lean * t * t
            half = wd * (1.0 - t) + 0.004
            d = np.abs(u - centre)
            blade = (d < half).astype(np.float32)
            # vertical gradient: dark root -> vivid tip (sRGB space)
            g = 0.26 + 0.34 * t + hue
            r = 0.10 + 0.14 * t + 0.5 * hue
            b = 0.06 + 0.08 * t
            # bright centre vein
            vein = np.clip(1.0 - d / np.maximum(half, 1e-5), 0.0, 1.0) ** 2
            g += 0.10 * vein
            r += 0.05 * vein
            m = blade > 0.5
            alpha[m] = 1.0
            col_r[m] = r[m]; col_g[m] = g[m]; col_b[m] = b[m]
        # colour-bleed: fill transparent texels with the mean blade colour
        # so mipmap minification never pulls in black fringes (the classic
        # cutout-foliage darkening bug).
        m = alpha > 0.5
        if np.any(m):
            mr = float(col_r[m].mean()); mg = float(col_g[m].mean())
            mb = float(col_b[m].mean())
            col_r[~m] = mr; col_g[~m] = mg; col_b[~m] = mb
        rgba = np.stack([col_r, col_g, col_b, alpha], axis=-1)
        return (np.clip(rgba, 0.0, 1.0) * 255.0).astype(np.uint8)

    # ------------------------------------------------------------------ #
    #  TerrainData — the CPU-side truth of one terrain node               #
    # ------------------------------------------------------------------ #
    #  heights : float32 (n,n), n = res+1, world XZ spans [-size/2, size/2]
    #  splat   : float32 (n,n,4) — channel order DIRT, ROCK, ASPHALT, SAND;
    #            the GRASS layer is the remainder (1 - sum(channels))
    #  grass_m : float32 (n,n) 0..1 — where 3D grass may grow (>0.35)
    class _TPTerrainData:
        def __init__(self, size=3000.0, res=896):
            self.size = float(size)
            self.res = int(res)
            self.n = self.res + 1
            self.heights = np.zeros((self.n, self.n), dtype=np.float32)
            self.splat = np.zeros((self.n, self.n, 4), dtype=np.float32)
            self.grass_m = np.ones((self.n, self.n), dtype=np.float32)
            # --- grass look/feel (user-tunable) ---
            self.grass_height = 0.30       # metres — requested default
            self.grass_density = 1.0       # multiplier 0.1..3
            self.grass_radius = 80.0       # draw distance (m) around camera
            self.grass_enabled = True
            # --- slope auto-rock (GameGuru-style), slope = 1 - normal.y ---
            self.slope_rock_start = 0.25   # ~41 degrees from vertical
            self.slope_rock_end = 0.50     # ~60 degrees -> full rock
            self.tiling = list(_TP_LAYER_TILING)
            # --- dirty tracking for partial GPU updates ---
            self.dirty_h = None            # (x0, z0, x1, z1) grid coords
            self.dirty_s = None
            self.grass_version = 0         # bumped when grass rules change
            self._grass_dirty_rect = None
            self._ramp_prev = None
            self.node = None               # back-ref to the SceneNode

        # ---------------- world <-> grid ----------------
        @property
        def cell(self):
            return self.size / float(self.res)

        def world_to_grid(self, wx, wz):
            gx = (float(wx) + self.size * 0.5) / self.cell
            gz = (float(wz) + self.size * 0.5) / self.cell
            return gx, gz

        def inside_world(self, wx, wz):
            h = self.size * 0.5
            return -h <= wx <= h and -h <= wz <= h

        def height_at_world(self, wx, wz):
            """Bilinear height at world XZ (terrain node must sit at the
            origin — it always does; it is created and kept at identity)."""
            gx, gz = self.world_to_grid(wx, wz)
            gx = _clamp(gx, 0.0, self.res - 1e-4)
            gz = _clamp(gz, 0.0, self.res - 1e-4)
            x0, z0 = int(gx), int(gz)
            x1 = min(x0 + 1, self.res)
            z1 = min(z0 + 1, self.res)
            fx, fz = gx - x0, gz - z0
            h = self.heights
            return float((h[z0, x0] * (1 - fx) + h[z0, x1] * fx) * (1 - fz)
                         + (h[z1, x0] * (1 - fx) + h[z1, x1] * fx) * fz)

        def slope_at_grid(self, gx, gz):
            x0 = int(_clamp(gx, 1, self.res - 1))
            z0 = int(_clamp(gz, 1, self.res - 1))
            h = self.heights
            dx = (h[z0, x0 + 1] - h[z0, x0 - 1]) / (2.0 * self.cell)
            dz = (h[z0 + 1, x0] - h[z0 - 1, x0]) / (2.0 * self.cell)
            ny = 1.0 / math.sqrt(dx * dx + dz * dz + 1.0)
            return 1.0 - ny          # 0 flat .. 1 vertical

        # ---------------- brush falloff region ----------------
        def _brush_region(self, wx, wz, radius):
            """(x0, z0, x1, z1, dist_grid, fall_grid) for a world-space
            circular brush; None when fully outside the terrain."""
            gx, gz = self.world_to_grid(wx, wz)
            gr = max(float(radius) / self.cell, 0.5)
            x0 = max(0, int(math.floor(gx - gr)))
            x1 = min(self.res, int(math.ceil(gx + gr)))
            z0 = max(0, int(math.floor(gz - gr)))
            z1 = min(self.res, int(math.ceil(gz + gr)))
            if x1 < x0 or z1 < z0:
                return None
            xs = np.arange(x0, x1 + 1, dtype=np.float32)[None, :]
            zs = np.arange(z0, z1 + 1, dtype=np.float32)[:, None]
            d = np.sqrt((xs - gx) ** 2 + (zs - gz) ** 2) / gr
            d = np.clip(d, 0.0, 1.0)
            fall = 0.5 + 0.5 * np.cos(d * math.pi)     # smooth, edge->0
            fall[d >= 1.0] = 0.0
            return x0, z0, x1, z1, fall

        def _mark_h(self, x0, z0, x1, z1):
            if self.dirty_h is None:
                self.dirty_h = [x0, z0, x1, z1]
            else:
                d = self.dirty_h
                d[0] = min(d[0], x0); d[1] = min(d[1], z0)
                d[2] = max(d[2], x1); d[3] = max(d[3], z1)
            self._mark_grass(x0, z0, x1, z1)

        def _mark_s(self, x0, z0, x1, z1):
            if self.dirty_s is None:
                self.dirty_s = [x0, z0, x1, z1]
            else:
                d = self.dirty_s
                d[0] = min(d[0], x0); d[1] = min(d[1], z0)
                d[2] = max(d[2], x1); d[3] = max(d[3], z1)

        def _mark_grass(self, x0, z0, x1, z1):
            self.grass_version += 1
            if self._grass_dirty_rect is None:
                self._grass_dirty_rect = [x0, z0, x1, z1]
            else:
                d = self._grass_dirty_rect
                d[0] = min(d[0], x0); d[1] = min(d[1], z0)
                d[2] = max(d[2], x1); d[3] = max(d[3], z1)

        # ---------------- sculpt brushes ----------------
        def sculpt(self, wx, wz, radius, strength, dt, mode,
                   target_h=None):
            reg = self._brush_region(wx, wz, radius)
            if reg is None:
                return
            x0, z0, x1, z1, fall = reg
            h = self.heights
            sub = h[z0:z1 + 1, x0:x1 + 1]
            rate = strength * dt
            if mode == "raise":
                sub += 6.0 * rate * fall
            elif mode == "dig":
                sub -= 6.0 * rate * fall
            elif mode == "smooth":
                # 4-neighbour box blur, blended by falloff
                p = np.pad(sub, 1, mode="edge")
                blur = (p[1:-1, 1:-1] + p[:-2, 1:-1] + p[2:, 1:-1]
                        + p[1:-1, :-2] + p[1:-1, 2:]) * 0.2
                k = np.clip(3.0 * rate, 0.0, 1.0) * fall
                sub += (blur - sub) * k
            elif mode == "stamp":
                # plateau: everything inside the brush walks toward the
                # height captured where the stroke started
                t0 = float(target_h if target_h is not None else 0.0)
                k = np.clip(4.0 * rate, 0.0, 1.0) * fall
                sub += (t0 - sub) * k
            self._mark_h(x0, z0, x1, z1)

        def ramp(self, ax, az, bx, bz, width, snapshot):
            """Raise a smooth ramp between anchor A and cursor B.  Applied
            from `snapshot` every frame (non-destructive live preview)."""
            if snapshot is None:
                return
            self.heights[:] = snapshot
            ax, az, bx, bz = float(ax), float(az), float(bx), float(bz)
            abx, abz = bx - ax, bz - az
            seg_len2 = abx * abx + abz * abz
            if seg_len2 < 1e-6:
                return
            ha = self.height_at_world(ax, az)
            hb = self.height_at_world(bx, bz)
            # bounding box of segment + width, in grid coords
            gax, gaz = self.world_to_grid(ax, az)
            gbx, gbz = self.world_to_grid(bx, bz)
            gr = max(float(width) / self.cell, 0.5)
            x0 = max(0, int(math.floor(min(gax, gbx) - gr)))
            x1 = min(self.res, int(math.ceil(max(gax, gbx) + gr)))
            z0 = max(0, int(math.floor(min(gaz, gbz) - gr)))
            z1 = min(self.res, int(math.ceil(max(gaz, gbz) + gr)))
            if x1 < x0 or z1 < z0:
                return
            xs = np.arange(x0, x1 + 1, dtype=np.float32)[None, :]
            zs = np.arange(z0, z1 + 1, dtype=np.float32)[:, None]
            # world coords of grid points
            wxg = xs * self.cell - self.size * 0.5
            wzg = zs * self.cell - self.size * 0.5
            t = ((wxg - ax) * abx + (wzg - az) * abz) / seg_len2
            t = np.clip(t, 0.0, 1.0)
            # perpendicular distance to the segment
            px = ax + t * abx
            pz = az + t * abz
            d = np.sqrt((wxg - px) ** 2 + (wzg - pz) ** 2) / float(width)
            d = np.clip(d, 0.0, 1.0)
            fall = 0.5 + 0.5 * np.cos(d * math.pi)
            fall[d >= 1.0] = 0.0
            target = ha + (hb - ha) * t
            sub = self.heights[z0:z1 + 1, x0:x1 + 1]
            sub += (target - sub) * fall
            self._mark_h(x0, z0, x1, z1)
            # The live preview restores the whole CPU grid from the snapshot
            # every frame: the PREVIOUS frame's ramp region must also be
            # re-uploaded or it stays as a ghost on the GPU while the CPU
            # (and physics) already reverted it.
            prev = getattr(self, "_ramp_prev", None)
            if prev is not None:
                self._mark_h(*prev)
            self._ramp_prev = (x0, z0, x1, z1)

        # ---------------- paint brushes ----------------
        # layer index: 0 grass, 1 dirt, 2 rock, 3 asphalt, 4 sand
        def paint(self, wx, wz, radius, strength, dt, layer):
            reg = self._brush_region(wx, wz, radius)
            if reg is None:
                return
            x0, z0, x1, z1, fall = reg
            k = np.clip(2.5 * strength * dt, 0.0, 1.0) * fall
            sp = self.splat[z0:z1 + 1, x0:x1 + 1, :]
            if layer == 0:
                # paint grass = fade every stored layer out, grass returns
                sp *= (1.0 - k)[..., None]
                gm = self.grass_m[z0:z1 + 1, x0:x1 + 1]
                gm[k > 0.02] = 1.0
                self._mark_grass(x0, z0, x1, z1)
            else:
                ch = layer - 1
                sp[..., ch] += k * (1.0 - sp[..., ch])
                # renormalise so sum(channels) <= 1 (grass = remainder)
                tot = sp.sum(axis=-1)
                over = tot > 1.0
                if np.any(over):
                    scale = np.ones_like(tot)
                    so = sp[over].sum(axis=-1)
                    st = sp[over, ch]
                    others = np.clip(so - st, 1e-6, None)
                    factor = np.clip((1.0 - st) / others, 0.0, 1.0)
                    tmp = sp[over]
                    for c in range(4):
                        if c != ch:
                            tmp[:, c] *= factor
                    sp[over] = tmp
                # rock / asphalt / sand kill the grass under the brush
                if layer in (2, 3, 4):
                    gm = self.grass_m[z0:z1 + 1, x0:x1 + 1]
                    gm[k > 0.02] = 0.0
                    self._mark_grass(x0, z0, x1, z1)
            self._mark_s(x0, z0, x1, z1)

        def erase_grass(self, wx, wz, radius, strength, dt):
            reg = self._brush_region(wx, wz, radius)
            if reg is None:
                return
            x0, z0, x1, z1, fall = reg
            k = np.clip(3.0 * strength * dt, 0.0, 1.0) * fall
            gm = self.grass_m[z0:z1 + 1, x0:x1 + 1]
            gm *= (1.0 - k)
            self._mark_grass(x0, z0, x1, z1)

        def regrow_grass(self):
            self.grass_m[:] = 1.0
            self.grass_version += 1
            self._grass_dirty_rect = [0, 0, self.res, self.res]

        # ---------------- serialisation ----------------
        def to_config(self) -> dict:
            return {
                "version": 2,
                "size": self.size,
                "resolution": self.res,
                "height_scale": 1.0,
                "seed": 0,
                "grass_height": float(self.grass_height),
                "grass_density": float(self.grass_density),
                "grass_radius": float(self.grass_radius),
                "grass_enabled": bool(self.grass_enabled),
                "slope_rock": [float(self.slope_rock_start),
                               float(self.slope_rock_end)],
                "tiling": [float(t) for t in self.tiling],
                "heights_b64": _tp_b64_encode(self.heights),
                "splat_b64": _tp_b64_encode(self.splat),
                "grass_b64": _tp_b64_encode(
                    (self.grass_m * 255.0).astype(np.uint8)),
            }

        @classmethod
        def from_config(cls, cfg: dict) -> "_TPTerrainData":
            size = float(cfg.get("size", 3000.0))
            res = int(cfg.get("resolution", 896))
            res = int(_clamp(res, 32, 2048))
            td = cls(size=size, res=res)
            td.grass_height = float(cfg.get("grass_height", 0.30))
            td.grass_density = float(cfg.get("grass_density", 1.0))
            td.grass_radius = float(cfg.get("grass_radius", 80.0))
            td.grass_enabled = bool(cfg.get("grass_enabled", True))
            sr = cfg.get("slope_rock") or [0.25, 0.50]
            td.slope_rock_start = float(sr[0])
            td.slope_rock_end = float(sr[1])
            til = cfg.get("tiling")
            if isinstance(til, (list, tuple)) and len(til) == 5:
                td.tiling = [float(t) for t in til]
            h = _tp_b64_decode(cfg.get("heights_b64", ""),
                               np.float32, (td.n, td.n))
            if h is not None:
                td.heights = h
            sp = _tp_b64_decode(cfg.get("splat_b64", ""),
                                np.float32, (td.n, td.n, 4))
            if sp is not None:
                td.splat = np.clip(sp, 0.0, 1.0)
            gm = _tp_b64_decode(cfg.get("grass_b64", ""),
                                np.uint8, (td.n, td.n))
            if gm is not None:
                td.grass_m = gm.astype(np.float32) / 255.0
            return td

    # ------------------------------------------------------------------ #
    #  Geometry-shader splat injection (runtime string patch, anchored)   #
    # ------------------------------------------------------------------ #
    _TP_SHADER_OK = False

    _TP_SPLAT_UNIFORMS = """
// ---- [TERRAINPATCH] terrain splatting (5 PBR layers + slope auto-rock) ----
uniform int u_splat_enabled;
uniform sampler2D u_splat_map;             // RGBA: dirt, rock, asphalt, sand
uniform sampler2DArray u_layer_albedo_arr; // 0 grass 1 dirt 2 rock 3 asphalt 4 sand
uniform sampler2DArray u_layer_normal_arr;
uniform float u_terrain_size;              // terrain width in metres
uniform vec4 u_layer_tiling_a;             // tiles/metre: grass dirt rock asphalt
uniform float u_layer_tiling_b;            // tiles/metre: sand
uniform vec4 u_layer_rough_a;              // roughness: grass dirt rock asphalt
uniform float u_layer_rough_b;             // roughness: sand
uniform vec2 u_slope_rock_range;           // auto-rock fade (start, end) slope

void _tp_splat(vec2 tuv, float slope,
               out vec3 alb_srgb, out vec3 tn, out float rough){
    vec4 w = texture(u_splat_map, tuv);
    float wg = clamp(1.0 - w.r - w.g - w.b - w.a, 0.0, 1.0);
    // GameGuru-style: steep slopes fade to rock automatically (fade in/out),
    // unless the artist deliberately paved asphalt / sand there.
    float pave = clamp(w.b + w.a, 0.0, 1.0);
    float auto_rock = smoothstep(u_slope_rock_range.x, u_slope_rock_range.y,
                                 slope);
    w.g = max(w.g, auto_rock * (1.0 - pave));
    float sum = wg + w.r + w.g + w.b + w.a + 1e-6;
    float s0 = wg / sum, s1 = w.r / sum, s2 = w.g / sum;
    float s3 = w.b / sum, s4 = w.a / sum;
    vec2 t0 = tuv * u_terrain_size * u_layer_tiling_a.x;
    vec2 t1 = tuv * u_terrain_size * u_layer_tiling_a.y;
    vec2 t2 = tuv * u_terrain_size * u_layer_tiling_a.z;
    vec2 t3 = tuv * u_terrain_size * u_layer_tiling_a.w;
    vec2 t4 = tuv * u_terrain_size * u_layer_tiling_b;
    alb_srgb = texture(u_layer_albedo_arr, vec3(t0, 0.0)).rgb * s0
             + texture(u_layer_albedo_arr, vec3(t1, 1.0)).rgb * s1
             + texture(u_layer_albedo_arr, vec3(t2, 2.0)).rgb * s2
             + texture(u_layer_albedo_arr, vec3(t3, 3.0)).rgb * s3
             + texture(u_layer_albedo_arr, vec3(t4, 4.0)).rgb * s4;
    // macro variation: the grass texture re-sampled at a giant scale breaks
    // tiling repetition for every layer (one extra fetch, zero extra VRAM)
    float macro = texture(u_layer_albedo_arr,
                          vec3(tuv * u_terrain_size * 0.0016, 0.0)).g;
    alb_srgb *= 0.80 + 0.40 * macro;
    vec3 n = texture(u_layer_normal_arr, vec3(t0, 0.0)).xyz * s0
           + texture(u_layer_normal_arr, vec3(t1, 1.0)).xyz * s1
           + texture(u_layer_normal_arr, vec3(t2, 2.0)).xyz * s2
           + texture(u_layer_normal_arr, vec3(t3, 3.0)).xyz * s3
           + texture(u_layer_normal_arr, vec3(t4, 4.0)).xyz * s4;
    tn = normalize(n * 2.0 - 1.0);
    rough = u_layer_rough_a.x * s0 + u_layer_rough_a.y * s1
          + u_layer_rough_a.z * s2 + u_layer_rough_a.w * s3
          + u_layer_rough_b * s4;
}
"""

    _TP_SPLAT_ALBEDO = """
    // [TERRAINPATCH] splat blend — flat zero cost path for normal meshes
    vec3 _tp_tn = vec3(0.0, 0.0, 1.0);
    float _tp_ro = 0.0;
    if (u_splat_enabled == 1){
        vec3 _tp_alb;
        _tp_splat(v_uv, 1.0 - clamp(normalize(v_normal).y, 0.0, 1.0),
                  _tp_alb, _tp_tn, _tp_ro);
        albedo = pow(_tp_alb, vec3(2.2));
    }
"""

    def _tp_inject_geometry_shader():
        """Extend GEOMETRY_FRAG with terrain splatting.  Pure anchored string
        surgery on the module global BEFORE any shader compiles; if an anchor
        is missing the patch stays disabled and terrain renders with its
        (harmless) plain base material instead of crashing anything."""
        global GEOMETRY_FRAG
        try:
            src_g = GEOMETRY_FRAG
            a1 = "uniform int   u_double_sided;"
            a2 = "albedo = pow(albedo, vec3(2.2));"
            a3 = ("    if (u_has_normal == 1){\n"
                  "        vec3 tn = texture(u_normal_tex, v_uv).xyz"
                  " * 2.0 - 1.0;")
            a4 = "roughness = clamp(roughness, 0.045, 1.0);"
            for a in (a1, a2, a3, a4):
                if a not in src_g:
                    _tp_log_once("shader_anchor",
                                 "[TERRAINPATCH] geometry shader anchor "
                                 "missing — splatting disabled (terrain "
                                 "falls back to a plain material).")
                    return False
            src_g = src_g.replace(a1, a1 + _TP_SPLAT_UNIFORMS, 1)
            src_g = src_g.replace(a2, a2 + _TP_SPLAT_ALBEDO, 1)
            src_g = src_g.replace(
                a3,
                "    if (u_splat_enabled == 1 || u_has_normal == 1){\n"
                "        vec3 tn = (u_splat_enabled == 1) ? _tp_tn :\n"
                "            texture(u_normal_tex, v_uv).xyz * 2.0 - 1.0;",
                1)
            src_g = src_g.replace(
                a4,
                a4 + "\n    if (u_splat_enabled == 1)\n"
                     "        roughness = clamp(_tp_ro, 0.045, 1.0);",
                1)
            GEOMETRY_FRAG = src_g
            return True
        except Exception as ex:
            _tp_log_once("shader_inject",
                         f"[TERRAINPATCH] shader injection failed: {ex}")
            return False

    _TP_SHADER_OK = _tp_inject_geometry_shader()

    # ------------------------------------------------------------------ #
    #  TerrainGL — GPU mesh + textures for one terrain node               #
    # ------------------------------------------------------------------ #
    class _TPTerrainGL:
        def __init__(self, td: "_TPTerrainData"):
            self.td = td
            self.mesh = None
            self.splat_tex = None
            self._splat_u8 = np.zeros((td.n, td.n, 4), dtype=np.uint8)
            self._positions = None
            self._interleaved = None

        # ----- one-time build -----
        def build(self, ctx):
            td = self.td
            n = td.n
            gx = np.arange(n, dtype=np.float32)
            world = gx * td.cell - td.size * 0.5
            X, Z = np.meshgrid(world, world)           # (n,n) rows=z
            pos = np.stack([X, td.heights, Z], axis=-1)
            positions = pos.reshape(-1, 3).astype(np.float32)
            normals = self._calc_normals(td.heights)
            uvs01 = (gx / float(td.res)).astype(np.float32)
            U, V = np.meshgrid(uvs01, uvs01)
            uvs = np.stack([U, V], axis=-1).reshape(-1, 2).astype(np.float32)
            res = td.res
            idx = np.arange(res * res, dtype=np.uint32)
            xi = idx % res
            zi = idx // res
            a = zi * n + xi
            b = a + 1
            c = a + n
            d = c + 1
            indices = np.empty((res * res, 6), dtype=np.uint32)
            indices[:, 0] = a; indices[:, 1] = c; indices[:, 2] = b
            indices[:, 3] = b; indices[:, 4] = c; indices[:, 5] = d
            indices = indices.ravel()
            mesh = Mesh.from_arrays(ctx, positions, normals, uvs, indices,
                                    "terrain_splat")
            # Adopt the exact arrays the mesh kept (CPU copies) so partial
            # updates mutate the same memory physics/tools read.
            self.mesh = mesh
            self._positions = mesh.cpu_positions \
                if mesh.cpu_positions is not None else positions
            # Rebuild a mutable interleaved staging buffer for VBO writes.
            iv = np.frombuffer(mesh._vertex_data, dtype=np.float32)
            self._interleaved = iv.reshape(-1, 12).copy()
            self._update_bounds()
            # splat texture (RGBA8, linear-filtered: free weight smoothing)
            self._splat_u8[:] = (np.clip(td.splat, 0.0, 1.0) * 255.0
                                 ).astype(np.uint8)
            self.splat_tex = ctx.texture((n, n), 4,
                                         self._splat_u8.tobytes(),
                                         dtype="f1")
            self.splat_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.splat_tex.repeat_x = False
            self.splat_tex.repeat_y = False
            td.dirty_h = None
            td.dirty_s = None

        def _calc_normals(self, h):
            """Central-difference normals for the whole (n,n) grid."""
            p = np.pad(h, 1, mode="edge")
            dx = (p[1:-1, 2:] - p[1:-1, :-2])
            dz = (p[2:, 1:-1] - p[:-2, 1:-1])
            ny = 2.0 * self.td.cell
            ln = np.sqrt(dx * dx + ny * ny + dz * dz) + 1e-8
            nrm = np.stack([-dx / ln, np.full_like(h, ny) / ln,
                            -dz / ln], axis=-1)
            return nrm.reshape(-1, 3).astype(np.float32)

        def _update_bounds(self):
            if self.mesh is None:
                return
            h = self.td.heights
            hmin, hmax = float(h.min()), float(h.max())
            half = self.td.size * 0.5
            self.mesh.bounding_box = (glm.vec3(-half, hmin - 0.5, -half),
                                      glm.vec3(half, hmax + 0.5, half))
            c = glm.vec3(0.0, (hmin + hmax) * 0.5, 0.0)
            r = math.sqrt(half * half * 2.0
                          + ((hmax - hmin) * 0.5 + 0.5) ** 2)
            self.mesh.bounding_sphere = (c, r)

        # ----- per-frame partial flush (only the dirty rect) -----
        def flush(self):
            td = self.td
            if self.mesh is None or self.mesh.vbo is None:
                td.dirty_h = None
                td.dirty_s = None
                return
            if td.dirty_h is not None:
                x0, z0, x1, z1 = td.dirty_h
                # +1 ring so normals on the boundary stay correct
                x0r = max(0, x0 - 1); z0r = max(0, z0 - 1)
                x1r = min(td.res, x1 + 1); z1r = min(td.res, z1 + 1)
                h = td.heights
                n = td.n
                p = np.pad(h, 1, mode="edge")
                for zi in range(z0r, z1r + 1):
                    # positions row
                    row_pos = h[zi, x0r:x1r + 1]
                    base = zi * n
                    self._positions[base + x0r:base + x1r + 1, 1] = row_pos
                    # normals row (central differences, edge-clamped)
                    dx = p[zi + 1, x0r + 2:x1r + 2 + 1] \
                        - p[zi + 1, x0r:x1r + 1]
                    dz = p[zi + 2, x0r + 1:x1r + 1 + 1] \
                        - p[zi, x0r + 1:x1r + 1 + 1]
                    ny = 2.0 * td.cell
                    ln = np.sqrt(dx * dx + ny * ny + dz * dz) + 1e-8
                    nrm = np.stack([-dx / ln,
                                    np.full_like(dx, ny) / ln,
                                    -dz / ln], axis=-1)
                    iv0 = base + x0r
                    cnt = x1r - x0r + 1
                    seg = self._interleaved[iv0:iv0 + cnt]
                    seg[:, 1] = row_pos
                    seg[:, 3:6] = nrm
                    self.mesh.vbo.write(seg.tobytes(),
                                        offset=iv0 * 48)
                # keep the physics/tools CPU copy in sync
                if self.mesh.cpu_positions is not None:
                    self.mesh.cpu_positions[:, 1] = h.reshape(-1)
                self._update_bounds()
                td.dirty_h = None
            if td.dirty_s is not None and self.splat_tex is not None:
                x0, z0, x1, z1 = td.dirty_s
                region = (np.clip(td.splat[z0:z1 + 1, x0:x1 + 1, :],
                                  0.0, 1.0) * 255.0).astype(np.uint8)
                self._splat_u8[z0:z1 + 1, x0:x1 + 1, :] = region
                try:
                    self.splat_tex.write(
                        np.ascontiguousarray(region).tobytes(),
                        viewport=(x0, z0, x1 - x0 + 1, z1 - z0 + 1))
                except Exception:
                    try:
                        self.splat_tex.write(self._splat_u8.tobytes())
                    except Exception as ex:
                        _tp_log_once("splat_write",
                                     f"[TERRAINPATCH] splat upload: {ex}")
                td.dirty_s = None

    # Live (node id) -> (_TPTerrainData, _TPTerrainGL) registry
    _TP_TERRAINS = {}

    def _tp_terrain_of(node):
        """Return the live _TPTerrainData for a terrain node (or None)."""
        if node is None:
            return None
        td = node.user_data.get("_tp_terrain_data") \
            if hasattr(node, "user_data") else None
        return td

    def _tp_gl_of(td, ctx):
        gl = td.__dict__.get("_tp_gl")
        if gl is None:
            gl = _TPTerrainGL(td)
            td.__dict__["_tp_gl"] = gl
        if gl.mesh is None and ctx is not None:
            try:
                gl.build(ctx)
            except Exception as ex:
                _tp_log_once("terrain_build",
                             f"[TERRAINPATCH] terrain mesh build failed: {ex}")
        return gl

    # Shared 5-layer texture arrays (built once per GL context)
    _TP_LAYER_CACHE = {}

    def _tp_layer_arrays(ctx):
        entry = _TP_LAYER_CACHE.get(id(ctx))
        if entry is not None:
            return entry
        try:
            layers = [_tp_build_layer_maps(i) for i in range(5)]
            s = _TP_TEX_SIZE
            alb = ctx.texture_array((s, s, 5), 4,
                                    b"".join(l[0].tobytes() for l in layers),
                                    dtype="f1")
            alb.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            alb.repeat_x = True
            alb.repeat_y = True
            alb.anisotropy = 4.0
            alb.build_mipmaps()
            nrm = ctx.texture_array((s, s, 5), 3,
                                    b"".join(l[1].tobytes() for l in layers),
                                    dtype="f1")
            nrm.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            nrm.repeat_x = True
            nrm.repeat_y = True
            nrm.anisotropy = 4.0
            nrm.build_mipmaps()
            _TP_LAYER_CACHE[id(ctx)] = (alb, nrm)
            _tp_info("Terrain layer PBR texture arrays built (5 x "
                     f"{s}x{s}, albedo+normal).")
            return alb, nrm
        except Exception as ex:
            _tp_log_once("layer_arrays",
                         f"[TERRAINPATCH] layer texture arrays failed: {ex}")
            return None

    # ------------------------------------------------------------------ #
    #  GPU grass — instanced crossed-quad blades, chunked LOD, wind,      #
    #  player/object push interaction.  Forward-rendered into the HDR     #
    #  scene target so depth, bloom, tonemap and FXAA all apply.          #
    # ------------------------------------------------------------------ #
    _TP_GRASS_VERT = """#version 330 core
// [TERRAINPATCH] grass blade vertex shader
layout(location = 0) in vec3 in_pos;   // blade card local (-w..w, 0..1, -w..w)
layout(location = 1) in vec2 in_uv;
layout(location = 2) in vec4 i_base;   // xyz world root, w height scale
layout(location = 3) in vec4 i_aux;    // x yaw, y phase, z colour var, w lod fade
uniform mat4 u_view;
uniform mat4 u_proj;
uniform float u_time;
uniform float u_grass_height;          // metres (default 0.30)
uniform vec4 u_pushers[12];            // xyz world pos, w radius
uniform int u_pusher_count;
out vec2 v_uv;
out float v_colvar;
out float v_bend;
out float v_fade;
void main(){
    float tip = in_pos.y;                       // 0 root .. 1 top
    float c = cos(i_aux.x);
    float s = sin(i_aux.x);
    vec3 pr = vec3(in_pos.x * c - in_pos.z * s,
                   in_pos.y,
                   in_pos.x * s + in_pos.z * c);
    float h = u_grass_height * i_base.w;
    vec3 wp = i_base.xyz + pr * vec3(1.0, h, 1.0);
    // wind: two sines, stronger at the tip
    float sway = sin(u_time * 1.9 + i_aux.y)
               + 0.5 * sin(u_time * 3.7 + i_aux.y * 1.7);
    wp.x += sway * 0.030 * tip * tip;
    wp.z += sway * 0.022 * tip * tip;
    // push interaction: player / objects trample blades away + down
    vec2 away = vec2(0.0);
    float bend = 0.0;
    for (int i = 0; i < 12; i++){
        if (i >= u_pusher_count) break;
        vec3 P = u_pushers[i].xyz;
        float r = max(u_pushers[i].w, 1e-3);
        vec2 d = wp.xz - P.xz;
        float dist = length(d);
        float infl = clamp(1.0 - dist / r, 0.0, 1.0);
        if (infl > 0.0 && dist > 1e-4){
            away += (d / dist) * infl;
            bend = max(bend, infl);
        }
    }
    wp.xz += away * 0.20 * tip;
    wp.y -= bend * 0.40 * h * tip;
    v_uv = in_uv;
    v_colvar = i_aux.z;
    v_bend = bend;
    v_fade = i_aux.w;
    gl_Position = u_proj * u_view * vec4(wp, 1.0);
}
"""

    _TP_GRASS_FRAG = """#version 330 core
// [TERRAINPATCH] grass blade fragment shader (HDR, cutout, screen-door LOD)
in vec2 v_uv;
in float v_colvar;
in float v_bend;
in float v_fade;
uniform sampler2D u_grass_tex;
uniform vec3 u_sun_dir;      // direction the light travels
uniform vec3 u_sun_color;    // colour * intensity (linear HDR)
uniform vec3 u_ambient;
out vec4 frag_color;
float _tp_hash(vec2 p){
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}
void main(){
    // screen-door LOD fade: statistically thin distant blades, no popping
    if (v_fade < 0.999 && _tp_hash(gl_FragCoord.xy) > v_fade)
        discard;
    vec4 t = texture(u_grass_tex, v_uv);
    if (t.a < 0.38)
        discard;
    vec3 base = pow(t.rgb, vec3(2.2)) * mix(0.80, 1.25, v_colvar);
    // blades shade like tiny up-facing translucent surfaces (wrap lighting)
    float ndl = clamp(-u_sun_dir.y, 0.0, 1.0);
    vec3 col = base * (u_ambient + u_sun_color * (0.25 + 0.85 * ndl));
    col *= mix(1.0, 0.70, v_bend);              // trampled = darker
    col *= mix(0.45, 1.0, smoothstep(0.0, 0.55, 1.0 - v_uv.y)); // root AO
    frag_color = vec4(col, 1.0);
}
"""

    _TP_GRASS_CELL = 4.0            # metres per grass chunk
    _TP_GRASS_BASE_COUNT = 46       # blades per cell at LOD0, density 1.0
    _TP_GRASS_MAX_INST = 90000      # hard safety cap
    _TP_GRASS_CACHE_CAP = 7000      # LRU cap on generated cells

    class _TPGrassRenderer:
        """One renderer per terrain node.  Blades live in 4 m cells that are
        generated deterministically (hash-seeded) only within the LOD radius
        around the camera; far rings thin out; editing invalidates the cells
        under the brush."""

        def __init__(self, td: "_TPTerrainData"):
            self.td = td
            self.ctx = None
            self.prog = None
            self.tex = None
            self.blade_vbo = None
            self.inst_vbo = None
            self.vao = None
            self.capacity = 0
            self.count = 0
            self.cells = {}           # (cx, cz, lod) -> np.float32 (k,8)
            self._cam_cell = None
            self._seen_version = -1
            self._failed = False
            self._last_rebuild = 0.0

        # ---------------- GL setup ----------------
        def _ensure(self, ctx):
            if self._failed:
                return False
            if self.vao is not None and self.ctx is ctx:
                return True
            try:
                self.ctx = ctx
                self.prog = ctx.program(vertex_shader=_TP_GRASS_VERT,
                                        fragment_shader=_TP_GRASS_FRAG)
                img = _tp_build_grass_texture(128)
                self.tex = ctx.texture((128, 128), 4, img.tobytes(),
                                       dtype="f1")
                self.tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR,
                                   moderngl.LINEAR)
                self.tex.build_mipmaps()
                # two crossed cards, 12 non-indexed verts, 5 floats each
                w = 0.075                  # card half-width (15 cm tuft)
                v = []
                def quad(ax):
                    # axis 0: card spans x; axis 1: spans z (crossed)
                    if ax == 0:
                        p = [(-w, 0, 0), (w, 0, 0), (w, 1, 0),
                             (-w, 0, 0), (w, 1, 0), (-w, 1, 0)]
                    else:
                        p = [(0, 0, -w), (0, 0, w), (0, 1, w),
                             (0, 0, -w), (0, 1, w), (0, 1, -w)]
                    uvs = [(0, 1), (1, 1), (1, 0),
                           (0, 1), (1, 0), (0, 0)]
                    for (px, py, pz), (uu, vv) in zip(p, uvs):
                        v.extend([px, py, pz, uu, vv])
                quad(0)
                quad(1)
                data = np.asarray(v, dtype=np.float32)
                self.blade_vbo = ctx.buffer(data.tobytes())
                self.capacity = 4096
                self.inst_vbo = ctx.buffer(reserve=self.capacity * 32)
                self.vao = ctx.vertex_array(
                    self.prog,
                    [(self.blade_vbo, "3f 2f", "in_pos", "in_uv"),
                     (self.inst_vbo, "4f 4f /i", "i_base", "i_aux")])
                return True
            except Exception as ex:
                _tp_log_once("grass_init",
                             f"[TERRAINPATCH] grass init failed: {ex}")
                self._failed = True
                return False

        # ---------------- cell generation ----------------
        def _gen_cell(self, cx, cz, lod):
            td = self.td
            half = td.size * 0.5
            x0w = -half + cx * _TP_GRASS_CELL
            z0w = -half + cz * _TP_GRASS_CELL
            seed = ((cx * 73856093) ^ (cz * 19349663) ^ 0x5EED) & 0x7FFFFFFF
            rng = np.random.RandomState(seed)
            lod_mult = (1.0, 0.5, 0.25)[lod]
            count = int(round(_TP_GRASS_BASE_COUNT * lod_mult
                              * td.grass_density))
            count = max(4, min(160, count))
            px = rng.uniform(x0w, x0w + _TP_GRASS_CELL, count)
            pz = rng.uniform(z0w, z0w + _TP_GRASS_CELL, count)
            gx = (px + half) / td.cell
            gz = (pz + half) / td.cell
            gx = np.clip(gx, 0.0, td.res - 1e-4)
            gz = np.clip(gz, 0.0, td.res - 1e-4)
            x0 = gx.astype(np.int32); z0 = gz.astype(np.int32)
            x1 = np.minimum(x0 + 1, td.res); z1 = np.minimum(z0 + 1, td.res)
            fx = gx - x0; fz = gz - z0
            h = td.heights
            hy = ((h[z0, x0] * (1 - fx) + h[z0, x1] * fx) * (1 - fz)
                  + (h[z1, x0] * (1 - fx) + h[z1, x1] * fx) * fz)
            keep = td.grass_m[z0, x0] > 0.35
            # no grass on rock / asphalt / sand dominated ground
            sp = td.splat[z0, x0, :]
            keep &= sp[:, 1] < 0.35
            keep &= sp[:, 2] < 0.35
            keep &= sp[:, 3] < 0.35
            # no grass on steep slopes
            xc = np.clip(x0, 1, td.res - 1); zc = np.clip(z0, 1, td.res - 1)
            dx = (h[zc, xc + 1] - h[zc, xc - 1]) / (2.0 * td.cell)
            dz = (h[zc + 1, xc] - h[zc - 1, xc]) / (2.0 * td.cell)
            ny = 1.0 / np.sqrt(dx * dx + dz * dz + 1.0)
            keep &= (1.0 - ny) < 0.34
            idx = np.nonzero(keep)[0]
            if idx.size == 0:
                return np.zeros((0, 8), dtype=np.float32)
            k = idx.size
            out = np.zeros((k, 8), dtype=np.float32)
            out[:, 0] = px[idx]
            out[:, 1] = hy[idx]
            out[:, 2] = pz[idx]
            out[:, 3] = rng.uniform(0.75, 1.35, k)          # height scale
            out[:, 4] = rng.uniform(0.0, math.pi, k)        # yaw
            out[:, 5] = rng.uniform(0.0, math.pi * 2.0, k)  # wind phase
            out[:, 6] = rng.uniform(0.0, 1.0, k)            # colour var
            out[:, 7] = 1.0                                 # lod fade
            return out

        def _invalidate_edited(self):
            td = self.td
            r = td._grass_dirty_rect
            if r is None:
                return
            td._grass_dirty_rect = None
            half = td.size * 0.5
            x0, z0, x1, z1 = r
            cx0 = int((x0 * td.cell) // _TP_GRASS_CELL) - 1
            cx1 = int((x1 * td.cell) // _TP_GRASS_CELL) + 1
            cz0 = int((z0 * td.cell) // _TP_GRASS_CELL) - 1
            cz1 = int((z1 * td.cell) // _TP_GRASS_CELL) + 1
            for key in list(self.cells.keys()):
                if cx0 <= key[0] <= cx1 and cz0 <= key[1] <= cz1:
                    del self.cells[key]

        # ---------------- per-frame update ----------------
        def update(self, engine, camera):
            if self._failed:
                return
            td = self.td
            now = getattr(engine, "time", 0.0) if engine is not None else 0.0
            self._invalidate_edited()
            half = td.size * 0.5
            camx = float(camera.position.x)
            camz = float(camera.position.z)
            ccx = int(math.floor((camx + half) / _TP_GRASS_CELL))
            ccz = int(math.floor((camz + half) / _TP_GRASS_CELL))
            cam_cell = (ccx, ccz)
            version = (td.grass_version, round(td.grass_height, 3),
                       round(td.grass_density, 3), int(td.grass_radius))
            moved = cam_cell != self._cam_cell
            changed = version != self._seen_version
            # density is baked into generated cells: when it changes the
            # cell cache must be regenerated, not just re-collected
            if self._seen_version != -1 and len(version) > 2 and \
                    version[2] != self._seen_version[2]:
                self.cells.clear()
            # amortise rebuilds: at most ~5 per second unless idle
            if not (moved or changed):
                return
            if now - self._last_rebuild < 0.18 and self.count > 0:
                return
            self._last_rebuild = now
            self._cam_cell = cam_cell
            self._seen_version = version
            radius = float(_clamp(td.grass_radius, 20.0, 200.0))
            r_cells = int(math.ceil(radius / _TP_GRASS_CELL))
            r2 = radius * radius
            near2 = (radius * 0.40) ** 2
            mid2 = (radius * 0.70) ** 2
            packs = []
            total = 0
            n_cells = td.res * td.cell / _TP_GRASS_CELL
            for cz in range(ccz - r_cells, ccz + r_cells + 1):
                if cz < 0 or cz >= n_cells:
                    continue
                for cx in range(ccx - r_cells, ccx + r_cells + 1):
                    if cx < 0 or cx >= n_cells:
                        continue
                    # world-space centre distance
                    wx = -half + (cx + 0.5) * _TP_GRASS_CELL
                    wz = -half + (cz + 0.5) * _TP_GRASS_CELL
                    d2 = (wx - camx) ** 2 + (wz - camz) ** 2
                    if d2 > r2:
                        continue
                    lod = 0 if d2 < near2 else (1 if d2 < mid2 else 2)
                    key = (cx, cz, lod)
                    pack = self.cells.get(key)
                    if pack is None:
                        try:
                            pack = self._gen_cell(cx, cz, lod)
                        except Exception as ex:
                            _tp_log_once("grass_cell",
                                         f"[TERRAINPATCH] grass cell: {ex}")
                            pack = np.zeros((0, 8), dtype=np.float32)
                        self.cells[key] = pack
                    if len(pack):
                        packs.append(pack)
                        total += len(pack)
                        if total >= _TP_GRASS_MAX_INST:
                            break
                if total >= _TP_GRASS_MAX_INST:
                    break
            # LRU eviction
            if len(self.cells) > _TP_GRASS_CACHE_CAP:
                keys = list(self.cells.keys())
                keys.sort(key=lambda k: (k[0] - ccx) ** 2 + (k[1] - ccz) ** 2,
                          reverse=True)
                for k in keys[:len(self.cells) // 4]:
                    del self.cells[k]
            data = (np.concatenate(packs, axis=0)
                    if packs else np.zeros((0, 8), dtype=np.float32))
            # distance fade column (screen-door): 1 near, ->0 at the rim
            if len(data):
                dd = np.sqrt((data[:, 0] - camx) ** 2
                             + (data[:, 2] - camz) ** 2)
                data[:, 7] = np.clip((radius - dd) / 12.0, 0.0, 1.0)
            self.count = len(data)
            if self.vao is None:
                return
            need = max(4096, int(len(data)) * 2)
            if need > self.capacity:
                try:
                    old_vao = self.vao
                    self.inst_vbo.release()
                    self.inst_vbo = self.ctx.buffer(reserve=need * 32)
                    self.capacity = need
                    self.vao = self.ctx.vertex_array(
                        self.prog,
                        [(self.blade_vbo, "3f 2f", "in_pos", "in_uv"),
                         (self.inst_vbo, "4f 4f /i", "i_base", "i_aux")])
                    old_vao.release()
                except Exception as ex:
                    _tp_log_once("grass_grow",
                                 f"[TERRAINPATCH] grass vbo grow: {ex}")
                    self._failed = True
                    return
            try:
                if len(data):
                    self.inst_vbo.orphan(self.capacity * 32)
                    self.inst_vbo.write(data.tobytes())
            except Exception as ex:
                _tp_log_once("grass_write",
                             f"[TERRAINPATCH] grass vbo write: {ex}")

        # ---------------- draw ----------------
        def render(self, renderer, scene, camera, view, proj):
            if self.count <= 0 or self.vao is None:
                return
            td = self.td
            if not td.grass_enabled:
                return
            prog = self.prog
            prog["u_view"].write(view.to_bytes())
            prog["u_proj"].write(proj.to_bytes())
            prog["u_time"] = float(getattr(_TP_ENGINE, "time", 0.0))
            prog["u_grass_height"] = float(_clamp(td.grass_height,
                                                  0.02, 2.0))
            # pushers: camera (player proxy) + nearest dynamic bodies
            pushers = []
            cam = camera.position
            pushers.append((float(cam.x), float(cam.y), float(cam.z), 0.7))
            eng = _TP_ENGINE
            phys = getattr(eng, "physics", None) if eng is not None else None
            if phys is not None and getattr(eng, "_playing", False):
                try:
                    cands = []
                    for e in phys.entries:
                        body = getattr(e, "body", None)
                        if body is None or body.body_type != "dynamic":
                            continue
                        p = body.position
                        d2 = ((float(p.x) - float(cam.x)) ** 2
                              + (float(p.z) - float(cam.z)) ** 2)
                        cands.append((d2, p))
                    cands.sort(key=lambda t: t[0])
                    for _d2, p in cands[:11]:
                        pushers.append((float(p.x), float(p.y),
                                        float(p.z), 0.9))
                except Exception:
                    pass
            flat = [v for pk in pushers[:12] for v in pk]
            flat += [0.0] * (48 - len(flat))
            try:
                prog["u_pushers"].write(
                    np.asarray(flat, dtype=np.float32).tobytes())
            except Exception:
                for i in range(12):
                    try:
                        prog[f"u_pushers[{i}]"] = tuple(
                            flat[i * 4:i * 4 + 4])
                    except Exception:
                        pass
            prog["u_pusher_count"] = len(pushers[:12])
            sun = scene.sun if scene is not None else None
            if sun is not None:
                prog["u_sun_dir"].write(sun.direction.to_bytes())
                sc = sun.color
                si = float(sun.intensity)
                prog["u_sun_color"].write(
                    glm.vec3(sc.x * si, sc.y * si, sc.z * si).to_bytes())
            else:
                prog["u_sun_dir"].write(glm.vec3(0.0, -1.0, 0.0).to_bytes())
                prog["u_sun_color"].write(glm.vec3(0.0).to_bytes())
            prog["u_ambient"].write(
                glm.vec3(0.23, 0.24, 0.26).to_bytes())
            self.tex.use(location=0)
            prog["u_grass_tex"] = 0
            renderer.scene_fbo_depth.use()
            ctx = self.ctx
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.depth_func = "<="
            ctx.depth_mask = True
            ctx.disable(moderngl.BLEND)
            ctx.disable(moderngl.CULL_FACE)
            self.vao.render(moderngl.TRIANGLES, vertices=12,
                            instances=self.count)
            # Restore EXACTLY the state the post chain expects (mirrors the
            # forward pass exit: depth-mask on, blend off, depth test OFF) —
            # a leaked DEPTH_TEST would depth-kill the fullscreen post quads
            # against the window depth buffer on real displays.
            ctx.depth_func = "<"
            ctx.depth_mask = True
            ctx.disable(moderngl.BLEND)
            ctx.disable(moderngl.DEPTH_TEST)

    # node id -> grass renderer
    _TP_GRASS = {}

    def _tp_grass_for(td):
        gr = td.__dict__.get("_tp_grass")
        if gr is None:
            gr = _TPGrassRenderer(td)
            td.__dict__["_tp_grass"] = gr
        return gr

    _TP_ENGINE = None

    # ------------------------------------------------------------------ #
    #  Ray vs heightfield (editor brush picking)                          #
    # ------------------------------------------------------------------ #
    def _tp_ray_terrain(td, ro, rd, max_t=5000.0):
        """Coarse walk + bisect refine against the live heightfield.
        Returns (x, z, h) world hit or None."""
        step = td.cell * 0.9
        t = 0.0
        prev_t = 0.0
        prev_clearance = None          # py - h at the previous sample
        entered = False
        h_max = float(td.heights.max()) if td.heights.size else 0.0
        while t < max_t:
            px = ro.x + rd.x * t
            py = ro.y + rd.y * t
            pz = ro.z + rd.z * t
            if td.inside_world(px, pz):
                entered = True
                h = td.height_at_world(px, pz)
                clearance = py - h
                # Count only a genuine above->below crossing: starting (or
                # being) below the surface is not a hit.
                if clearance <= 0.0 and prev_clearance is not None \
                        and prev_clearance > 0.0:
                    lo, hi = prev_t, t
                    for _ in range(22):
                        mid = 0.5 * (lo + hi)
                        mx = ro.x + rd.x * mid
                        my = ro.y + rd.y * mid
                        mz = ro.z + rd.z * mid
                        if my <= td.height_at_world(mx, mz):
                            hi = mid
                        else:
                            lo = mid
                    hx = ro.x + rd.x * hi
                    hz = ro.z + rd.z * hi
                    return hx, hz, td.height_at_world(hx, hz)
                prev_clearance = clearance
                if py > h_max + 50.0 and rd.y > 0.0:
                    return None
            elif entered:
                # walked past the far edge of the terrain
                return None
            else:
                prev_clearance = None
            prev_t = t
            t += step
        return None

    # ------------------------------------------------------------------ #
    #  Brush ring overlay (world-space circle that hugs the terrain)      #
    # ------------------------------------------------------------------ #
    class _TPRing:
        _MAX = 4096

        def __init__(self):
            self._ctx = None
            self._prog = None
            self._vbo = None
            self._vao = None
            self._failed = False

        def _ensure(self, ctx):
            if self._failed:
                return False
            if self._vao is not None and self._ctx is ctx:
                return True
            try:
                self._prog = ShaderLib.get(ctx, "line")
                if self._prog is None:
                    self._failed = True
                    return False
                self._vbo = ctx.buffer(reserve=self._MAX * 6 * 4,
                                       dynamic=True)
                self._vao = ctx.vertex_array(
                    self._prog,
                    [(self._vbo, "3f 3f", "in_position", "in_color")])
                self._ctx = ctx
                return True
            except Exception:
                self._failed = True
                return False

        def draw(self, engine, camera, lines):
            ctx = getattr(engine, "ctx", None)
            if ctx is None or not lines or not self._ensure(ctx):
                return
            try:
                count = min(len(lines) // 6, self._MAX)
                self._vbo.write(np.asarray(lines[:count * 6],
                                           dtype=np.float32).tobytes())
                self._prog["u_view"].write(camera.view().to_bytes())
                self._prog["u_proj"].write(camera.projection().to_bytes())
                self._prog["u_model"].write(glm.mat4(1.0).to_bytes())
                ctx.disable(moderngl.DEPTH_TEST)
                ctx.disable(moderngl.BLEND)
                self._vao.render(moderngl.LINES, vertices=count)
                ctx.enable(moderngl.DEPTH_TEST)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Terrain Editor — panel, tools, brushes (editor mode only)          #
    # ------------------------------------------------------------------ #
    _TP_TOOL_COLORS = {
        "raise":  (0.35, 0.75, 0.35, 1.0),
        "dig":    (0.85, 0.35, 0.30, 1.0),
        "smooth": (0.35, 0.70, 0.80, 1.0),
        "stamp":  (0.85, 0.65, 0.25, 1.0),
        "ramp":   (0.80, 0.80, 0.30, 1.0),
        "paint":  (0.45, 0.65, 0.90, 1.0),
        "erase":  (0.80, 0.35, 0.75, 1.0),
    }
    _TP_LAYER_COLORS = (
        (0.22, 0.52, 0.20, 1.0),    # grass
        (0.42, 0.30, 0.16, 1.0),    # dirt
        (0.45, 0.45, 0.47, 1.0),    # rock
        (0.16, 0.16, 0.18, 1.0),    # asphalt
        (0.72, 0.62, 0.38, 1.0),    # sand
    )

    class _TPTerrainEditor:
        TOOLS = (("raise", "Raise"), ("dig", "Dig"), ("smooth", "Smooth"),
                 ("stamp", "Stamp"), ("ramp", "Ramp"), ("paint", "Paint"),
                 ("erase", "EraseGrass"))
        LAYERS = (("Grass", 0), ("Dirt", 1), ("Rock", 2),
                  ("Asphalt", 3), ("Sand", 4))

        def __init__(self, engine):
            self.engine = engine
            self.panel_open = False
            self.tool = "raise"
            self.layer = 0
            self.brush_size = 12.0        # metres — visible ring
            self.brush_strength = 0.5
            self.hover = None             # (x, z, h) world hit under cursor
            self.stroke_active = False
            self.stroke_snapshot = None   # ramp preview base
            self.stroke_target_h = None   # stamp plateau height
            self.ramp_anchor = None
            self._ring = _TPRing()
            self._last_t = 0.0

        # ---------------- terrain lookup / creation ----------------
        def terrain_node(self):
            sc = getattr(self.engine, "scene", None)
            if sc is None:
                return None
            for n in (sc.special_nodes() if hasattr(sc, "special_nodes") else sc.root.traverse()):
                if getattr(n, "is_terrain", False) \
                        and _tp_terrain_of(n) is not None:
                    return n
            return None

        def terrain_data(self):
            return _tp_terrain_of(self.terrain_node())

        def ensure_terrain(self):
            node = self.terrain_node()
            if node is not None:
                return node
            try:
                return self.engine.create_terrain(size=3000.0,
                                                  resolution=896)
            except Exception as ex:
                _tp_log_once("auto_create",
                             f"[TERRAINPATCH] terrain create failed: {ex}")
                return None

        # ---------------- panel ----------------
        def toggle(self):
            if self.panel_open:
                self.close()
            else:
                self.open()

        def open(self):
            self.panel_open = True
            self.ensure_terrain()

        def close(self):
            self.panel_open = False
            self._end_stroke(commit=True)
            self.hover = None

        # ---------------- strokes ----------------
        def _begin_stroke(self, hit):
            td = self.terrain_data()
            self.stroke_active = True
            self.stroke_snapshot = None
            self.stroke_target_h = None
            self.ramp_anchor = None
            if td is not None and hit is not None:
                if self.tool == "ramp":
                    self.ramp_anchor = (hit[0], hit[1])
                    self.stroke_snapshot = td.heights.copy()
                elif self.tool == "stamp":
                    self.stroke_target_h = float(hit[2])

        def _end_stroke(self, commit=True):
            if self.tool == "ramp" and not commit \
                    and self.stroke_snapshot is not None:
                td = self.terrain_data()
                if td is not None:
                    td.heights[:] = self.stroke_snapshot
                    td._mark_h(0, 0, td.res, td.res)
            self.stroke_active = False
            self.stroke_snapshot = None
            self.stroke_target_h = None
            self.ramp_anchor = None

        # ---------------- per-frame (from Renderer.render wrap) ----------
        def tick_paint(self):
            eng = self.engine
            inp = getattr(eng, "input", None)
            if inp is None:
                return
            self.hover = None
            mx, my = inp.mouse_pos
            ux = mx / float(eng.width)
            uy = 1.0 - my / float(eng.height)
            ui = getattr(eng, "ui", None)
            try:
                if ui is not None and ui.point_in_panels(ux, uy):
                    return
            except Exception:
                pass
            td = self.terrain_data()
            if td is None:
                return
            try:
                ro, rd = eng._mouse_ray((mx, my))
            except Exception:
                return
            self.hover = _tp_ray_terrain(td, ro, rd)
            if self.hover is None:
                return
            now = getattr(eng, "time", 0.0)
            dt = _clamp(now - self._last_t, 0.0, 0.1) if self._last_t else 1/60
            self._last_t = now
            if not (self.stroke_active and getattr(inp, "lmb", False)):
                return
            hx, hz, hh = self.hover
            try:
                if self.tool == "paint":
                    td.paint(hx, hz, self.brush_size, self.brush_strength,
                             dt, self.layer)
                elif self.tool == "erase":
                    td.erase_grass(hx, hz, self.brush_size,
                                   self.brush_strength, dt)
                elif self.tool == "ramp":
                    if self.ramp_anchor is not None \
                            and self.stroke_snapshot is not None:
                        td.ramp(self.ramp_anchor[0], self.ramp_anchor[1],
                                hx, hz, self.brush_size,
                                self.stroke_snapshot)
                elif self.tool == "stamp":
                    td.sculpt(hx, hz, self.brush_size, self.brush_strength,
                              dt, "stamp", target_h=self.stroke_target_h)
                else:
                    td.sculpt(hx, hz, self.brush_size, self.brush_strength,
                              dt, self.tool)
            except Exception as ex:
                _tp_log_once("brush", f"[TERRAINPATCH] brush: {ex}")

        # ---------------- input ----------------
        def mouse_down(self, button, pos) -> bool:
            """True when the click was consumed as a terrain stroke."""
            eng = self.engine
            if button != 1 or not self.panel_open:
                return False
            if getattr(eng, "_playing", False):
                return False
            inp = getattr(eng, "input", None)
            if inp is not None and bool(getattr(inp, "alt", False)):
                return False                     # Alt+LMB = camera orbit
            ux = pos[0] / float(eng.width)
            uy = 1.0 - pos[1] / float(eng.height)
            ui = getattr(eng, "ui", None)
            try:
                if ui is not None and ui.point_in_panels(ux, uy):
                    return False                 # panel/button clicks: normal
            except Exception:
                pass
            vg = getattr(eng, "view_gizmo", None)
            if vg is not None:
                try:
                    if vg.point_in_region(pos, eng.width, eng.height):
                        return False             # navball: normal
                except Exception:
                    pass
            if self.hover is None:
                td = self.terrain_data()
                if td is None:
                    return False
                try:
                    ro, rd = eng._mouse_ray(pos)
                    self.hover = _tp_ray_terrain(td, ro, rd)
                except Exception:
                    return False
            if self.hover is None:
                return False
            self._begin_stroke(self.hover)
            if inp is not None:
                inp.lmb = True
                inp.click_just_pressed = True
                inp.click_pos = pos
            return True

        def mouse_up(self, button):
            if button == 1 and self.stroke_active:
                self._end_stroke(commit=True)

        def on_key(self, key) -> bool:
            """True when the key was consumed by the terrain editor."""
            if not self.panel_open:
                return False
            try:
                import pygame as _pg
                if key == _pg.K_ESCAPE:
                    if self.stroke_active:
                        self._end_stroke(commit=False)
                    else:
                        self.close()
                    return True
                if key == _pg.K_LEFTBRACKET:
                    self.brush_size = _clamp(self.brush_size / 1.25,
                                             0.5, 200.0)
                    return True
                if key == _pg.K_RIGHTBRACKET:
                    self.brush_size = _clamp(self.brush_size * 1.25,
                                             0.5, 200.0)
                    return True
            except Exception:
                pass
            return False

        # ---------------- ring overlay ----------------
        def draw_ring(self, engine):
            if self.hover is None or not self.panel_open:
                return
            td = self.terrain_data()
            if td is None:
                return
            hx, hz, _hh = self.hover
            col = _TP_TOOL_COLORS.get(self.tool, (1, 1, 1, 1))
            if self.tool == "paint":
                col = _TP_LAYER_COLORS[self.layer]
            r = self.brush_size
            lines = []
            seg_n = 56
            prev = None
            for i in range(seg_n + 1):
                a = i / seg_n * math.pi * 2.0
                x = hx + math.cos(a) * r
                z = hz + math.sin(a) * r
                if td.inside_world(x, z):
                    y = td.height_at_world(x, z) + 0.15
                else:
                    y = td.height_at_world(hx, hz) + 0.15
                if prev is not None:
                    lines.extend([prev[0], prev[1], prev[2],
                                  col[0], col[1], col[2],
                                  x, y, z, col[0], col[1], col[2]])
                prev = (x, y, z)
            # centre cross
            y0 = td.height_at_world(hx, hz) + 0.15
            cr = max(r * 0.12, 0.3)
            for dx, dz in ((-cr, 0.0), (cr, 0.0)):
                pass
            lines.extend([hx - cr, y0, hz, col[0], col[1], col[2],
                          hx + cr, y0, hz, col[0], col[1], col[2]])
            lines.extend([hx, y0, hz - cr, col[0], col[1], col[2],
                          hx, y0, hz + cr, col[0], col[1], col[2]])
            self._ring.draw(engine, engine.camera, lines)

        # ---------------- immediate-mode panel ----------------
        def draw_panel(self, ui):
            T = THEME
            td = self.terrain_data()
            # Panel sits bottom-left of the viewport: clear of the hierarchy
            # (x<0.18), console (y<0.12), navbar (y>0.928), inspector
            # (x>0.82) and the BUILDING EDITOR panel (x 0.56..0.81).
            ui.panel(UIRect(0.188, 0.150, 0.318, 0.530), T["panel_dark"])
            ui.panel(UIRect(0.188, 0.678, 0.318, 0.002), T["border"])
            x0 = 0.198
            w_all = 0.298
            y = 0.646
            rh = 0.028

            def _txt(label, color=T["text"], dy=0.024):
                nonlocal y
                ui.text(UIRect(x0, y, w_all, 0.020), label, color)
                y -= dy

            def _stepper(label, fmt, minus, plus):
                nonlocal y
                ui.text(UIRect(x0, y, 0.118, rh), label, T["text"])
                ui.button(UIRect(x0 + 0.122, y, 0.028, rh), "-", minus,
                          T["button"])
                ui.text(UIRect(x0 + 0.154, y + 0.004, 0.088, 0.022), fmt,
                        T["text_bright"])
                ui.button(UIRect(x0 + 0.246, y, 0.028, rh), "+", plus,
                          T["button"])
                y -= rh + 0.007

            _txt("TERRAIN EDITOR  — GameGuru style", T["text_bright"],
                 0.028)
            if td is None:
                _txt("No terrain in this scene yet.", T["text"], 0.030)
                ui.button(UIRect(x0, y, w_all, 0.040),
                          "CREATE 3x3 KM TERRAIN",
                          lambda: self.ensure_terrain(), _hex("#2E7CD6"))
                y -= 0.052
                _txt("Flat start · sculpt + paint by hand.",
                     T["text_dim"], 0.024)
                _txt("896 res · realistic player scale.", T["text_dim"],
                     0.024)
                return
            _txt(f"Size {td.size/1000.0:.1f}x{td.size/1000.0:.1f} km · "
                 f"res {td.res} · cell {td.cell:.2f} m", T["text_dim"],
                 0.026)

            # ---- tools ----
            _txt("Sculpt / Paint tools:", T["accent"], 0.024)
            for i, (key, label) in enumerate(self.TOOLS):
                row, coln = divmod(i, 4)
                bw4 = 0.0715
                rect = UIRect(x0 + coln * (bw4 + 0.004),
                              y - row * (rh + 0.006), bw4, rh)
                colr = (T["accent_dim"] if self.tool == key
                        else T["button"])
                ui.button(rect, label, lambda k=key: setattr(self, "tool", k),
                          colr)
            y -= 2 * (rh + 0.006) + 0.008

            # ---- layers ----
            _txt("Ground layer (auto-fades with slope rock):", T["accent"],
                 0.024)
            for i, (label, li) in enumerate(self.LAYERS):
                bw5 = 0.0568
                rect = UIRect(x0 + i * (bw5 + 0.004), y, bw5, rh)
                colr = (_TP_LAYER_COLORS[li] if self.layer == li
                        and self.tool == "paint"
                        else T["button"])
                ui.button(rect, label,
                          lambda l=li: (setattr(self, "layer", l),
                                        setattr(self, "tool", "paint")),
                          colr)
            y -= rh + 0.010

            # ---- brush ----
            _stepper("Brush size", f"{self.brush_size:.1f} m",
                     lambda: setattr(self, "brush_size",
                                     _clamp(self.brush_size / 1.25,
                                            0.5, 200.0)),
                     lambda: setattr(self, "brush_size",
                                     _clamp(self.brush_size * 1.25,
                                            0.5, 200.0)))
            _stepper("Strength", f"{int(round(self.brush_strength*100))}%",
                     lambda: setattr(self, "brush_strength",
                                     _clamp(self.brush_strength - 0.05,
                                            0.05, 1.0)),
                     lambda: setattr(self, "brush_strength",
                                     _clamp(self.brush_strength + 0.05,
                                            0.05, 1.0)))

            # ---- grass ----
            _txt("3D grass (GPU instanced · LOD · reactive):",
                 T["green_text"], 0.024)
            _stepper("Grass height", f"{td.grass_height*100:.0f} cm",
                     lambda: setattr(td, "grass_height",
                                     _clamp(td.grass_height - 0.02,
                                            0.05, 1.0)),
                     lambda: setattr(td, "grass_height",
                                     _clamp(td.grass_height + 0.02,
                                            0.05, 1.0)))
            _stepper("Density", f"{td.grass_density:.1f}x",
                     lambda: setattr(td, "grass_density",
                                     _clamp(td.grass_density - 0.1,
                                            0.1, 3.0)),
                     lambda: setattr(td, "grass_density",
                                     _clamp(td.grass_density + 0.1,
                                            0.1, 3.0)))
            _stepper("Draw dist", f"{td.grass_radius:.0f} m",
                     lambda: setattr(td, "grass_radius",
                                     _clamp(td.grass_radius - 10.0,
                                            20.0, 200.0)),
                     lambda: setattr(td, "grass_radius",
                                     _clamp(td.grass_radius + 10.0,
                                            20.0, 200.0)))
            half_w = 0.147
            ui.button(UIRect(x0, y, half_w, rh),
                      f"Grass: {'ON' if td.grass_enabled else 'OFF'}",
                      lambda: setattr(td, "grass_enabled",
                                      not td.grass_enabled),
                      T["play_green"] if td.grass_enabled else T["button"])
            ui.button(UIRect(x0 + half_w + 0.006, y, half_w, rh),
                      "Regrow grass", lambda: td.regrow_grass(), T["button"])
            y -= rh + 0.010

            # ---- slope auto-rock ----
            deg0 = math.degrees(math.acos(_clamp(1.0 - td.slope_rock_start,
                                                 0.0, 1.0)))
            _stepper("Rock slope", f"> {deg0:.0f} deg",
                     lambda: _tp_slope_adj(td, -2.0),
                     lambda: _tp_slope_adj(td, +2.0))

            _txt("LMB paint · [ ] brush size · ESC close",
                 T["text_dim"], 0.024)
            ui.button(UIRect(x0, y, w_all, 0.034), "CLOSE TERRAIN EDITOR",
                      self.close, T["danger"])

    def _tp_slope_adj(td, delta_deg):
        try:
            d0 = math.degrees(math.acos(_clamp(1.0 - td.slope_rock_start,
                                               0.0, 1.0)))
            d0 = _clamp(d0 + delta_deg, 20.0, 75.0)
            td.slope_rock_start = 1.0 - math.cos(math.radians(d0))
            td.slope_rock_end = 1.0 - math.cos(
                math.radians(_clamp(d0 + 18.0, 25.0, 85.0)))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Engine integration — creation, physics, IO, per-frame hooks        #
    # ------------------------------------------------------------------ #

    # ---- 1. retire the random terrain generator -------------------------
    def _tp_flat_heightmap(resolution: int, seed: int = 42,
                           height_scale: float = 10.0) -> np.ndarray:
        """The random terrain generator is REMOVED.  Every legacy caller
        (Terrain.generate, HeightfieldCollider) now receives a FLAT field,
        so rendered mesh and physics surface stay perfectly consistent."""
        n = int(resolution) + 1
        return np.zeros((n, n), dtype=np.float32)

    _heightmap_noise = _tp_flat_heightmap

    def _tp_terrain_generate(self):
        """Terrain.generate replacement: flat start, hand-sculpted after."""
        self.heights = _tp_flat_heightmap(self.resolution)
        self._build_mesh()

    Terrain.generate = _tp_terrain_generate

    # ---- 2. terrain creation (v2: flat 3x3 km + splat + grass) ----------
    def _tp_make_terrain_material(td):
        mat = Material(name="TerrainSplat",
                       base_color=(0.22, 0.33, 0.14, 1.0),
                       metallic=0.0, roughness=0.9)
        mat._tp_terrain_data = td
        return mat

    def _tp_attach_terrain(engine, node, td):
        td.node = node
        node.user_data["_tp_terrain_data"] = td
        node.user_data["terrain_config"] = {
            "version": 2, "size": td.size, "resolution": td.res,
            "height_scale": 1.0, "seed": 0,
        }
        gl = _tp_gl_of(td, getattr(engine, "ctx", None))
        if gl is not None and gl.mesh is not None:
            node.mesh = gl.mesh
        node.is_terrain = True
        node.mesh_type = None
        node.material = _tp_make_terrain_material(td)
        return node

    def _tp_create_terrain(self, size: float = 3000.0, resolution: int = 896,
                           height_scale: float = 1.0, seed: int = 0):
        """Create the flat, hand-editable 3x3 km terrain (replaces the old
        random-noise terrain entirely)."""
        resolution = int(_clamp(int(resolution), 32, 2048))
        td = _TPTerrainData(size=float(size), res=resolution)
        node = SceneNode("Terrain", parent=self.scene.root)
        _tp_attach_terrain(self, node, td)
        _tp_info(f"Terrain created: {size:.0f}x{size:.0f} m, res "
                 f"{resolution} — flat start, sculpt with the TERRAIN panel.")
        return node

    def _tp_create_terrain_node(self, node: SceneNode, config: dict):
        """Project-load path: hydrate heights/splat/grass from the saved
        config (v2); legacy v1 configs load FLAT (random noise retired)."""
        try:
            td = _TPTerrainData.from_config(config or {})
        except Exception as ex:
            _tp_log_once("terrain_load",
                         f"[TERRAINPATCH] terrain data decode failed "
                         f"({ex}); using flat terrain.")
            td = _TPTerrainData(size=float((config or {}).get("size",
                                                             3000.0)),
                                res=int((config or {}).get("resolution",
                                                           896)))
        _tp_attach_terrain(self, node, td)

    HeavenEngine.create_terrain = _tp_create_terrain
    HeavenEngine.create_terrain_node = _tp_create_terrain_node

    # ---- 3. physics: bind the heightfield collider to LIVE heights ------
    try:
        _TP_PREV_MAYBE_ADD = PhysicsWorld._maybe_add

        def _tp_maybe_add(self, node):
            try:
                if getattr(node, "is_terrain", False) \
                        and getattr(node, "collider", None) is None:
                    td = _tp_terrain_of(node)
                    if td is not None:
                        hf = HeightfieldCollider(size=td.size, resolution=8)
                        # LIVE shared reference: sculpting updates collision
                        # with zero rebuild cost — no falling through, ever.
                        hf.heights = td.heights
                        hf.size = td.size
                        hf.resolution = td.res
                        hf._cell = td.cell
                        node.collider = hf
            except Exception as ex:
                _tp_log_once("hf_bind",
                             f"[TERRAINPATCH] heightfield bind: {ex}")
            return _TP_PREV_MAYBE_ADD(self, node)

        PhysicsWorld._maybe_add = _tp_maybe_add
    except Exception as _tp_ex:
        _tp_log_once("hf_wrap", f"[TERRAINPATCH] physics wrap: {_tp_ex}")


    # ---- 3b. player anti-slide: no gravity-drift on walkable slopes ------
    # The CharacterController's vertical (gravity) pass resolves ground
    # contacts by pushing the capsule out along the slope NORMAL, which has
    # a horizontal component on any slope — so every grounded frame nudged
    # the player slightly downhill.  The wrap below re-implements
    # _move_slide with a surgical carve-out: during the purely vertical
    # pass, resting on a WALKABLE slope (inside the slope limit) takes only
    # the VERTICAL part of the correction — zero horizontal drift, while
    # walking, jumping, step-assist, moving platforms and steep-slope
    # behaviour all keep the stock code path.  Pure math, zero FPS cost.
    # The stock _move_slide now lifts straight up on walkable ground in
    # EVERY pass (and no longer double-applies slides), which covers this
    # carve-out; the wrap below only installs over an older stock mover.
    try:
        if getattr(CharacterController, "_H3D_SLOPE_FIX", False):
            raise RuntimeError("skip: stock mover already slope-correct")
        _TP_PREV_MOVE_SLIDE = CharacterController._move_slide

        def _tp_cc_move_slide(self, pos: glm.vec3, delta: glm.vec3,
                              allow_step: bool = True) -> glm.vec3:
            try:
                remaining = glm.vec3(delta)
                for _bounce in range(3):
                    if glm.length(remaining) < 1e-9:
                        break
                    target = pos + remaining
                    hits = self._collide_capsule(target)
                    if not hits:
                        pos = target
                        break
                    hits.sort(key=lambda h: -h[1])
                    n, depth, _node = hits[0]
                    if n.y < -0.3 and remaining.y > 0.0:
                        self.hit_ceiling = True
                    # [TERRAINPATCH:anti_slide] idle-on-slope fix.
                    if (not allow_step
                            and remaining.x == 0.0 and remaining.z == 0.0
                            and remaining.y < 0.0
                            and n.y > self.slope_cos):
                        # Vertical-only rest contact on a walkable slope:
                        # lift straight up out of the surface, keep X/Z.
                        lift = (depth + self.skin) / max(float(n.y), 0.3)
                        pos = glm.vec3(target.x, target.y + lift, target.z)
                        break
                    pos = target + n * (depth + self.skin)
                    # Step assist must run BEFORE slide projection (stock).
                    if allow_step and self.step_offset > 0.0 \
                            and abs(n.y) < 0.7:
                        horiz = glm.vec3(remaining.x, 0.0, remaining.z)
                        if glm.length(horiz) > 1e-9:
                            stepped = self._try_step(pos, horiz, n)
                            if stepped is not None:
                                pos, remaining = stepped
                                continue
                    into = glm.dot(remaining, n)
                    if into < 0.0:
                        remaining = remaining - n * into
                    else:
                        remaining = glm.vec3(0.0)
                return pos
            except Exception:
                # any edge case: stock behaviour, never a crash
                return _TP_PREV_MOVE_SLIDE(self, pos, delta,
                                           allow_step=allow_step)

        CharacterController._move_slide = _tp_cc_move_slide
    except RuntimeError:
        pass
    except Exception as _tp_ex:
        _tp_log_once("cc_wrap", f"[TERRAINPATCH] anti-slide wrap: {_tp_ex}")

    # ---- 4. save: sync live terrain data into the JSON config -----------
    try:
        _TP_PREV_SAVE = ProjectSerializer.save

        def _tp_save(cls, scene, path, cam_state, settings):
            try:
                for node in scene.root.traverse():
                    if getattr(node, "is_terrain", False):
                        td = _tp_terrain_of(node)
                        if td is not None:
                            node.user_data["terrain_config"] = td.to_config()
            except Exception as ex:
                _tp_log_once("terrain_save",
                             f"[TERRAINPATCH] terrain save sync: {ex}")
            return _TP_PREV_SAVE(scene, path, cam_state, settings)

        ProjectSerializer.save = classmethod(_tp_save)
    except Exception as _tp_ex:
        _tp_log_once("save_wrap", f"[TERRAINPATCH] save wrap: {_tp_ex}")

    # ---- 5. splat binding in the geometry pass ---------------------------
    # Every sampler the injected shader declares is ALWAYS bound to a
    # complete texture of the right type (shared layer arrays or a 1x1
    # fallback) on units 13/14/15 — strict drivers and llvmpipe draw-
    # validation stay happy, and non-terrain meshes cost three binds.
    _TP_DUMMY_TEX = {}

    def _tp_dummy_splat(ctx):
        t = _TP_DUMMY_TEX.get(id(ctx))
        if t is None:
            try:
                t = ctx.texture((1, 1), 4, bytes(4),
                                dtype="f1")
                t.filter = (moderngl.NEAREST, moderngl.NEAREST)
                _TP_DUMMY_TEX[id(ctx)] = t
            except Exception:
                return None
        return t

    def _tp_dummy_array(ctx):
        t = _TP_DUMMY_TEX.get((id(ctx), "arr"))
        if t is None:
            try:
                z = np.zeros((1, 1, 5, 4), dtype=np.uint8)
                z[..., 2] = 255          # neutral-ish normal (0,0,1)
                t = ctx.texture_array((1, 1, 5), 4, z.tobytes(),
                                      dtype="f1")
                t.filter = (moderngl.NEAREST, moderngl.NEAREST)
                _TP_DUMMY_TEX[(id(ctx), "arr")] = t
            except Exception:
                return None
        return t

    try:
        _TP_PREV_BIND_MAT = Renderer._bind_material

        def _tp_bind_material(self, prog, mat):
            _TP_PREV_BIND_MAT(self, prog, mat)
            if not _TP_SHADER_OK:
                return
            try:
                td = getattr(mat, "_tp_terrain_data", None)
                ctx = getattr(self, "ctx", None)
                arrs = (_tp_layer_arrays(ctx) or _tp_dummy_array(ctx)) \
                    if ctx is not None else None
                gl = _tp_gl_of(td, ctx) if td is not None else None
                splat = gl.splat_tex if (gl is not None
                                         and gl.splat_tex is not None) \
                    else (_tp_dummy_splat(ctx) if ctx is not None else None)
                if arrs is None or splat is None:
                    prog["u_splat_enabled"] = 0
                    return
                alb, nrm = arrs
                alb.use(location=13)
                nrm.use(location=14)
                splat.use(location=15)
                prog["u_layer_albedo_arr"] = 13
                prog["u_layer_normal_arr"] = 14
                prog["u_splat_map"] = 15
                if td is None:
                    prog["u_splat_enabled"] = 0
                    return
                t = td.tiling
                prog["u_layer_tiling_a"] = (float(t[0]), float(t[1]),
                                            float(t[2]), float(t[3]))
                prog["u_layer_tiling_b"] = float(t[4])
                prog["u_layer_rough_a"] = tuple(
                    float(x) for x in _TP_LAYER_ROUGH[:4])
                prog["u_layer_rough_b"] = float(_TP_LAYER_ROUGH[4])
                prog["u_terrain_size"] = float(td.size)
                prog["u_slope_rock_range"] = (float(td.slope_rock_start),
                                              float(td.slope_rock_end))
                prog["u_splat_enabled"] = 1
            except Exception as ex:
                _tp_log_once("bind_mat",
                             f"[TERRAINPATCH] splat bind: {ex}")

        Renderer._bind_material = _tp_bind_material
    except Exception as _tp_ex:
        _tp_log_once("bind_wrap", f"[TERRAINPATCH] bind wrap: {_tp_ex}")

    # ---- 6. per-frame flush + grass update (Renderer.render wrap) --------
    def _tp_frame_tick(renderer, scene, camera):
        eng = _TP_ENGINE
        for node in (scene.special_nodes() if hasattr(scene, "special_nodes") else scene.root.traverse()):
            if not getattr(node, "is_terrain", False):
                continue
            td = _tp_terrain_of(node)
            if td is None:
                continue
            gl = _tp_gl_of(td, renderer.ctx)
            if gl is not None:
                gl.flush()
            gr = _tp_grass_for(td)
            if gr._ensure(renderer.ctx):
                gr.update(eng, camera)
        if eng is not None and getattr(eng, "mode", None) == "editor" \
                and not getattr(eng, "_playing", False):
            ed = getattr(eng, "_tp_editor", None)
            if ed is not None and ed.panel_open:
                ed.tick_paint()

    try:
        _TP_PREV_RENDER = Renderer.render

        def _tp_render(self, scene, camera):
            try:
                _tp_frame_tick(self, scene, camera)
            except Exception as ex:
                _tp_log_once("frame_tick",
                             f"[TERRAINPATCH] frame tick: {ex}")
            return _TP_PREV_RENDER(self, scene, camera)

        Renderer.render = _tp_render
    except Exception as _tp_ex:
        _tp_log_once("render_wrap", f"[TERRAINPATCH] render wrap: {_tp_ex}")

    # ---- 7. grass draw in the forward pass -------------------------------
    try:
        _TP_PREV_FORWARD = Renderer._render_forward_transparent

        def _tp_forward(self, scene, camera, view, proj):
            _TP_PREV_FORWARD(self, scene, camera, view, proj)
            try:
                for node in (scene.special_nodes() if hasattr(scene, "special_nodes") else scene.root.traverse()):
                    if not getattr(node, "visible", True) \
                            or not getattr(node, "is_terrain", False):
                        continue
                    td = _tp_terrain_of(node)
                    if td is None:
                        continue
                    gr = _tp_grass_for(td)
                    if gr.count > 0:
                        gr.render(self, scene, camera, view, proj)
            except Exception as ex:
                _tp_log_once("grass_draw",
                             f"[TERRAINPATCH] grass draw: {ex}")

        Renderer._render_forward_transparent = _tp_forward
    except Exception as _tp_ex:
        _tp_log_once("forward_wrap",
                     f"[TERRAINPATCH] forward wrap: {_tp_ex}")

    # ---- 8. engine init: editor holder + module engine ref ---------------
    try:
        _TP_PREV_INIT = HeavenEngine.__init__

        def _tp_init(self, *a, **kw):
            _TP_PREV_INIT(self, *a, **kw)
            self._tp_editor = None
            global _TP_ENGINE
            _TP_ENGINE = self

        HeavenEngine.__init__ = _tp_init
    except Exception as _tp_ex:
        _tp_log_once("init_wrap", f"[TERRAINPATCH] init wrap: {_tp_ex}")

    def _tp_toggle_terrain_editor(self):
        """The blue TERRAIN navbar button."""
        if getattr(self, "mode", None) != "editor":
            return
        ed = getattr(self, "_tp_editor", None)
        if ed is None:
            ed = _TPTerrainEditor(self)
            self._tp_editor = ed
        ed.toggle()

    HeavenEngine.toggle_terrain_editor = _tp_toggle_terrain_editor

    # ---- 9. editor panel + brush ring (_build_ui wrap) --------------------
    try:
        _TP_PREV_BUILD_UI = HeavenEngine._build_ui

        def _tp_build_ui(self):
            _TP_PREV_BUILD_UI(self)
            try:
                if getattr(self, "mode", None) != "editor" \
                        or getattr(self, "_playing", False):
                    return
                ui = getattr(self, "ui", None)
                if ui is None:
                    return
                ed = getattr(self, "_tp_editor", None)
                if ed is not None and ed.panel_open:
                    if getattr(self, "_vt", None) is None:     # the Vitrail editor has its own panel
                        ed.draw_panel(ui)
                    ed.draw_ring(self)
                # Floating fallback button: only when the patcher could NOT
                # place the navbar button (marker config set by
                # Terrainupdate.py).  Never used in the runtime file.
                if (globals().get("_TERRAINPATCH_NAVBAR", True) == "FLOAT"
                        and getattr(self, "_vt", None) is None):
                    ui.button(UIRect(0.186, 0.890, 0.078, 0.034),
                              "TERRAIN", self.toggle_terrain_editor,
                              _hex("#2E7CD6"))
            except Exception as ex:
                _tp_log_once("build_ui",
                             f"[TERRAINPATCH] panel draw: {ex}")

        HeavenEngine._build_ui = _tp_build_ui
    except Exception as _tp_ex:
        _tp_log_once("ui_wrap", f"[TERRAINPATCH] ui wrap: {_tp_ex}")

    # ---- 10. click intercept for terrain strokes --------------------------
    try:
        _TP_PREV_ON_MOUSE_DOWN = HeavenEngine._on_mouse_down

        def _tp_on_mouse_down(self, button, pos):
            try:
                ed = getattr(self, "_tp_editor", None)
                if ed is not None and ed.panel_open \
                        and getattr(self, "mode", None) == "editor" \
                        and not getattr(self, "_playing", False):
                    if ed.mouse_down(button, pos):
                        return        # consumed: no gizmo drag, no repick
            except Exception as ex:
                _tp_log_once("mdown",
                             f"[TERRAINPATCH] mouse down: {ex}")
            return _TP_PREV_ON_MOUSE_DOWN(self, button, pos)

        HeavenEngine._on_mouse_down = _tp_on_mouse_down
    except Exception as _tp_ex:
        _tp_log_once("mdown_wrap", f"[TERRAINPATCH] mdown wrap: {_tp_ex}")

    try:
        _TP_PREV_ON_MOUSE_UP = HeavenEngine._on_mouse_up

        def _tp_on_mouse_up(self, button, pos):
            try:
                ed = getattr(self, "_tp_editor", None)
                if ed is not None:
                    ed.mouse_up(button)
            except Exception:
                pass
            return _TP_PREV_ON_MOUSE_UP(self, button, pos)

        HeavenEngine._on_mouse_up = _tp_on_mouse_up
    except Exception as _tp_ex:
        _tp_log_once("mup_wrap", f"[TERRAINPATCH] mup wrap: {_tp_ex}")

    try:
        _TP_PREV_ON_KEY = HeavenEngine._on_key

        def _tp_on_key(self, key):
            try:
                ed = getattr(self, "_tp_editor", None)
                if ed is not None and getattr(self, "mode", None) == "editor" \
                        and not getattr(self, "_playing", False) \
                        and ed.on_key(key):
                    return
            except Exception:
                pass
            return _TP_PREV_ON_KEY(self, key)

        HeavenEngine._on_key = _tp_on_key
    except Exception as _tp_ex:
        _tp_log_once("key_wrap", f"[TERRAINPATCH] key wrap: {_tp_ex}")

    _tp_info("TerrainPatch v2 installed: GameGuru-style terrain editor, "
             "5-layer PBR splatting, reactive GPU grass, live heightfield "
             "physics.  Random terrain generation removed.")
# [TERRAINPATCH:gameguru_terrain_editor_v2 END]


# [TERRAINPATCH:config] navbar button installed
_TERRAINPATCH_NAVBAR = True



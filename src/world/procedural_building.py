# @heaven3d-part world/procedural_building.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 15c - PROCEDURAL BUILDING SYSTEM v3  (AAA procedural PBR textures +
#               high-quality procedural meshes + FPS-Creator grid painting +
#               Gizmo manual control + BUILDING EDITOR panel; appended by
#               FinalPatch, append-only, monkey-patch integration only)
# #############################################################################
#
# [FINALPATCH:procedural_sector_v3 BEGIN]
#
# INTEGRATION (conceptual hooks, all installed via monkey-patching at the
# bottom of this section - ZERO existing engine lines were modified):
#   1. ProjectSerializer._deserialize_node is wrapped: after the original
#      loader runs, a node whose mesh_type == "procedural_module" is handed to
#      engine.procedural_generator.rebuild_module(node), which deterministically
#      regenerates Mesh + Material + Collider/RigidBody (+ glass child) from
#      node.mesh_params.  (Conceptually: `elif mt == "procedural_module":
#      engine.procedural_generator.rebuild_module(node)`.)
#   2. HeavenEngine.__init__ is wrapped: after the original constructor runs,
#      `self.procedural_generator = ProceduralSectorGenerator(self)` is set,
#      `HeavenEngine.add_procedural_sector(origin, biome, floors=1,
#      footprint=(8, 8))` is installed, and the engine instance is published
#      to the module-level _FP_ENGINE reference (used by the per-frame
#      EditorGrid.render ghost hook, which has no engine argument).
#   3. HeavenEngine._build_ui is wrapped: after the original UI is emitted, a
#      "BUILDING EDITOR" button is drawn in the free top-right navbar slot
#      (x 0.935..0.995, y 0.944..0.986), toggling the v2 immediate-mode panel
#      (GENERATE / PAINT / MANUAL modes, module palette, biome selector,
#      floor +/-, GENERATE SECTOR, SNAP TO GRID).  All UI code is guarded
#      with `getattr(engine, "ui", None)` so runtime mode is unaffected.
#   4. HeavenEngine._on_mouse_down is wrapped (THE click intercept point,
#      same gating idiom as ColliderShapeEditor): while PAINT mode is active
#      (panel open AND mode == "paint" - the explicit panel_open x mode
#      state machine) and the click is not on a UI panel / ViewGizmo
#      navball, LMB paints, Ctrl+LMB or RMB erases, and the event is
#      consumed (no gizmo drag, no scene reselection).  When the panel is
#      closed or paint mode is off the original handler runs byte-for-byte
#      untouched.
#   5. EditorGrid.render is wrapped (called every editor frame, editor-only,
#      not playing): after the original grid draw, the paint tool updates its
#      snapped ghost from engine.input.mouse_pos (R or [/] yaw-rotate the
#      ghost in 90-degree steps) and draws the snapped footprint line-box
#      with the shared "line" shader + dynamic VBO (LightGizmos pattern).
#   6. Gizmo.end_drag is wrapped: after a manual W/E/R drag on a procedural
#      module, occupancy is re-synced (old cells freed, new cells claimed from
#      the final world transform) and mesh_params (grid_pos / rotation_index)
#      are rewritten so serialization stays exact.
#   7. HeavenEngine._delete_node is wrapped: deleting a procedural module
#      through the normal editor path frees its occupancy cells.
#   8. HeavenEngine._on_key is wrapped: while PAINT mode is active, R
#      rotates the paint ghost +90 deg (and is consumed, so the gizmo does
#      NOT switch to SCALE); every other key - and R whenever paint mode is
#      off - falls through to the original handler untouched.
#
# v3 physics notes:
#   * stairwell modules carry the engine's native MeshCollider (triangles
#     re-sourced from the node's own shared Mesh at build time, so the
#     collider matches the visual EXACTLY - real risers + treads).  The
#     engine serialises MeshCollider as {"type": "mesh"} and restores it
#     via Collider.from_dict (triangles re-sourced from the mesh), so the
#     round-trip works; rebuild_module re-applies the plan anyway.
#   * window_frame and glass_pane are STATIC thin BoxColliders whose
#     z-ranges tile the window plane without overlapping; NO RigidBody
#     anywhere on a window (the v2 dynamic mass-5 body is gone, including
#     from stale v2 saves - rebuild_module recomputes their plans).
#
# Design notes:
#   * The "geometry_inst" shader and the automatic instancing fast-path in
#     Renderer.render ALREADY EXIST (SECTION 15b).  This section does NOT
#     re-add them; it reuses one cached Mesh per (module_key, biome) and one
#     cached Material per biome across all identical module instances so the
#     renderer's (id(mesh), id(material)) grouping picks them up for free.
#   * Everything GL/pygame-related is created LAZILY on the first
#     generate()/rebuild_module()/paint() call - importing this module
#     performs no GL work, creates no pygame surfaces, compiles no shaders.
#   * Save/load round-trips through the stock serializer: textures stored as
#     ("bytes", png) serialise to "__embedded__" and load back as None, which
#     is fine because rebuild_module deterministically regenerates everything
#     from node.mesh_params (seed = FNV-1a of biome/footprint/floors/seed).
#   * All textures are tileable by construction: every lattice is periodic
#     (mod-wrapped), all shifts use np.roll, all resampling wraps.
#   * Noise kernels follow the engine's own optional-numba pattern
#     (_HAS_NUMBA + @_jit(cache=True) from SECTION 0): numba-accelerated when
#     available, pure-NumPy vectorized fallback otherwise.

# ---------------------------------------------------------------------------
#  Shared constants / determinism / optional-numba mirror
# ---------------------------------------------------------------------------

_FP_CELL = 2.0             # 1 grid unit = 2.0 world meters
_FP_FLOOR_H = 3.0          # floor-to-floor height (m)
_FP_WALL_T = 0.12          # wall core thickness (m)
_FP_TEX_SIZE = 128         # procedural texture resolution (tileable)
_FP_ENGINE = None          # published by the HeavenEngine.__init__ wrapper


def _fp_fnv1a(*parts):
    """Deterministic 32-bit FNV-1a hash -> stable generation seed."""
    h = 2166136261
    for part in parts:
        for ch in str(part):
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
    return h


# Engine optional-numba pattern (SECTION 0): reuse its _HAS_NUMBA/_jit
# verbatim when present; otherwise mirror the identical passthrough so this
# section stays import-safe everywhere (stub namespaces included).
try:
    _FP_HAS_NUMBA = bool(_HAS_NUMBA)
except NameError:
    _FP_HAS_NUMBA = False

try:
    _fp_jit = _jit                       # the engine's own decorator
except NameError:
    _fp_jit = None

if _fp_jit is None:
    if _FP_HAS_NUMBA:
        try:
            from numba import njit as _fp_njit

            def _fp_jit(*args, **kwargs):
                return _fp_njit(*args, **kwargs)
        except Exception:
            _FP_HAS_NUMBA = False
            _fp_jit = None

if _fp_jit is None:
    def _fp_jit(*args, **kwargs):
        """Passthrough mirror of the engine's _jit when numba is absent."""
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def _decorator(func):
            return func
        return _decorator


# ---------------------------------------------------------------------------
#  _fp_tex - advanced procedural PBR texture synthesis (pure math, no
#  external images): periodic gradient noise, Worley/cellular F2-F1, FBM
#  with domain warping, ridged multifractal, Sobel normal maps, height-
#  derived roughness, multi-tap horizon AO.  All tileable.
# ---------------------------------------------------------------------------

@_fp_jit(cache=True)
def _fp_k_hash(ix, iy, seed):
    """Integer lattice hash -> [0, 1).  numba-compatible scalar kernel.
    Multipliers are chosen so every intermediate product fits int64."""
    h = (ix * 374761393 + iy * 668265263
         + (seed & 0x7FFFFFFF) * 2654435761) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return h / 4294967296.0


@_fp_jit(cache=True)
def _fp_k_grad(size, period, seed, out):
    """Periodic gradient (Perlin-style) noise kernel, writes out[y, x]."""
    for py in range(size):
        fy = (py + 0.5) / size * period
        y0 = int(math.floor(fy))
        ty = fy - y0
        ty = ty * ty * (3.0 - 2.0 * ty)
        y0m = y0 % period
        y1m = (y0 + 1) % period
        for px in range(size):
            fx = (px + 0.5) / size * period
            x0 = int(math.floor(fx))
            tx = fx - x0
            tx = tx * tx * (3.0 - 2.0 * tx)
            x0m = x0 % period
            x1m = (x0 + 1) % period
            acc = 0.0
            for corner in range(4):
                cx = x0m if corner < 2 else x1m
                cy = y0m if corner % 2 == 0 else y1m
                dx = fx - (x0 if corner < 2 else x0 + 1)
                dy = fy - (y0 if corner % 2 == 0 else y0 + 1)
                ang = _fp_k_hash(cx, cy, seed) * 6.283185307179586
                dot = math.cos(ang) * dx + math.sin(ang) * dy
                w = ((1.0 - tx) if corner < 2 else tx) * \
                    ((1.0 - ty) if corner % 2 == 0 else ty)
                acc += dot * w
            v = 0.5 + 0.5 * acc * 1.4142135623730951
            out[py, px] = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
    return out


@_fp_jit(cache=True)
def _fp_k_worley(size, cells, seed, jitter, f1, f2):
    """Periodic Worley/cellular kernel: F1 and F2 distances (cell units)."""
    for py in range(size):
        fy = (py + 0.5) / size * cells
        iy = int(math.floor(fy))
        for px in range(size):
            fx = (px + 0.5) / size * cells
            ix = int(math.floor(fx))
            b1 = 1.0e9
            b2 = 1.0e9
            for oy in range(-1, 2):
                cy = (iy + oy) % cells
                for ox in range(-1, 2):
                    cx = (ix + ox) % cells
                    h1 = _fp_k_hash(cx, cy, seed)
                    h2 = _fp_k_hash(cx, cy, seed + 101)
                    fxp = cx + 0.5 + jitter * (h1 - 0.5)
                    fyp = cy + 0.5 + jitter * (h2 - 0.5)
                    d = math.sqrt((fxp - fx) * (fxp - fx)
                                  + (fyp - fy) * (fyp - fy))
                    if d < b1:
                        b2 = b1
                        b1 = d
                    elif d < b2:
                        b2 = d
            f1[py, px] = b1
            f2[py, px] = b2
    return f1, f2

def _fp_hash_grid(ix, iy, seed):
    """Vectorized lattice hash (uint32 wrap-around == scalar kernel mask)."""
    ux = np.asarray(ix, dtype=np.uint32)
    uy = np.asarray(iy, dtype=np.uint32)
    seed_term = np.uint32(((int(seed) & 0x7FFFFFFF) * 2654435761)
                          & 0xFFFFFFFF)
    h = (ux * np.uint32(374761393) + uy * np.uint32(668265263)
         + seed_term)
    h = (h ^ (h >> np.uint32(13))) * np.uint32(1274126177)
    h = h ^ (h >> np.uint32(16))
    return h.astype(np.float64) / 4294967296.0


def _fp_grad_np(size, period, seed):
    """Pure-NumPy periodic gradient noise (fallback when numba absent)."""
    t = (np.arange(size, dtype=np.float64) + 0.5) / size * period
    i0 = np.floor(t).astype(np.int64)
    f = t - i0
    f = f * f * (3.0 - 2.0 * f)
    i0m = i0 % period
    i1m = (i0 + 1) % period
    out = np.zeros((size, size), dtype=np.float64)
    for corner in range(4):
        cx = i0m if corner < 2 else i1m
        cy = i0m if corner % 2 == 0 else i1m
        dx = (t - (i0 if corner < 2 else i0 + 1))[None, :]
        dy = (t - (i0 if corner % 2 == 0 else i0 + 1))[:, None]
        ang = _fp_hash_grid(*np.meshgrid(cx, cy, indexing="xy"), seed) * \
            6.283185307179586
        dot = np.cos(ang) * dx + np.sin(ang) * dy
        wx = ((1.0 - f) if corner < 2 else f)[None, :]
        wy = ((1.0 - f) if corner % 2 == 0 else f)[:, None]
        out += dot * wx * wy
    return np.clip(0.5 + 0.5 * out * 1.4142135623730951, 0.0, 1.0)


def _fp_worley_np(size, cells, seed, jitter):
    """Pure-NumPy periodic Worley F1/F2 (fallback when numba absent)."""
    t = (np.arange(size, dtype=np.float64) + 0.5) / size * cells
    X, Y = np.meshgrid(t, t)            # X varies along columns, Y rows
    cxi = np.floor(X).astype(np.int64)
    cyi = np.floor(Y).astype(np.int64)
    f1 = np.full((size, size), 1.0e9)
    f2 = np.full((size, size), 1.0e9)
    for oy in range(-1, 2):
        for ox in range(-1, 2):
            cx = (cxi + ox) % cells
            cy = (cyi + oy) % cells
            h1 = _fp_hash_grid(cx, cy, seed)
            h2 = _fp_hash_grid(cx, cy, seed + 101)
            fxp = cx + 0.5 + jitter * (h1 - 0.5)
            fyp = cy + 0.5 + jitter * (h2 - 0.5)
            d = np.sqrt((fxp - X) ** 2 + (fyp - Y) ** 2)
            f2 = np.minimum(f2, np.maximum(d, f1))
            f1 = np.minimum(f1, d)
    return f1, f2


class _fp_tex:
    """Namespace for the texture-synthesis pipeline (module-style)."""

    # -- primitives ------------------------------------------------------
    @staticmethod
    def gradient(size, period, seed):
        period = max(1, int(period))
        if _FP_HAS_NUMBA:
            try:
                out = np.zeros((size, size), dtype=np.float64)
                _fp_k_grad(int(size), period, int(seed), out)
                return out
            except Exception:
                pass
        return _fp_grad_np(int(size), period, int(seed))

    @staticmethod
    def worley(size, cells, seed, jitter=1.0):
        cells = max(1, int(cells))
        if _FP_HAS_NUMBA:
            try:
                f1 = np.zeros((size, size), dtype=np.float64)
                f2 = np.zeros((size, size), dtype=np.float64)
                _fp_k_worley(int(size), cells, int(seed),
                             float(jitter), f1, f2)
                return f1, f2
            except Exception:
                pass
        return _fp_worley_np(int(size), cells, int(seed), float(jitter))

    @staticmethod
    def fbm(size, period, octaves, seed):
        """Fractal Brownian motion over periodic gradient noise, [0, 1]."""
        total = np.zeros((size, size), dtype=np.float64)
        amp = 1.0
        norm = 0.0
        p = max(1, int(period))
        for o in range(max(1, int(octaves))):
            total += amp * _fp_tex.gradient(size, p, seed + o * 131)
            norm += amp
            amp *= 0.5
            p *= 2
        return total / max(norm, 1e-9)

    @staticmethod
    def ridged(size, period, octaves, seed):
        """Ridged multifractal: abs-inverted octaves, [0, 1]."""
        total = np.zeros((size, size), dtype=np.float64)
        amp = 0.5
        norm = 0.0
        p = max(1, int(period))
        for o in range(max(1, int(octaves))):
            g = _fp_tex.gradient(size, p, seed + o * 733)
            total += amp * (1.0 - np.abs(2.0 * g - 1.0))
            norm += amp
            amp *= 0.5
            p *= 2
        return np.clip(total / max(norm, 1e-9), 0.0, 1.0)

    @staticmethod
    def _resample_wrap(field, sx, sy):
        """Periodic bilinear resample of `field` at float coords (sx, sy)."""
        size = field.shape[0]
        x = np.mod(sx, size)
        y = np.mod(sy, size)
        x0 = np.floor(x).astype(np.int64) % size
        y0 = np.floor(y).astype(np.int64) % size
        x1 = (x0 + 1) % size
        y1 = (y0 + 1) % size
        fx = x - np.floor(x)
        fy = y - np.floor(y)
        top = field[y0, x0] * (1.0 - fx) + field[y0, x1] * fx
        bot = field[y1, x0] * (1.0 - fx) + field[y1, x1] * fx
        return top * (1.0 - fy) + bot * fy

    @staticmethod
    def warped_fbm(size, period, octaves, seed, warp):
        """FBM with domain warping: fbm(p + warp * gradient(p))."""
        wx = _fp_tex.gradient(size, max(1, int(period)), seed + 1000) - 0.5
        wy = _fp_tex.gradient(size, max(1, int(period)), seed + 2000) - 0.5
        inner = _fp_tex.fbm(size, period, octaves, seed)
        t = np.arange(size, dtype=np.float64)
        sx = t[None, :] + float(warp) * wx
        sy = t[:, None] + float(warp) * wy
        return _fp_tex._resample_wrap(inner, sx, sy)

    # -- map derivation --------------------------------------------------
    @staticmethod
    def normals_from_height(height, strength=2.0):
        """Tangent-space normal map (RGB uint8) from a height field via
        periodic 3x3 Sobel finite differences."""
        h = height.astype(np.float64)
        # Sobel Gx = right column - left column (with centre weights 2)
        gx = ((np.roll(np.roll(h, 1, 0), -1, 1)
               + 2.0 * np.roll(h, -1, 1)
               + np.roll(np.roll(h, -1, 0), -1, 1))
              - (np.roll(np.roll(h, 1, 0), 1, 1)
                 + 2.0 * np.roll(h, 1, 1)
                 + np.roll(np.roll(h, -1, 0), 1, 1)))
        gy = ((np.roll(np.roll(h, -1, 0), 1, 1)
               + 2.0 * np.roll(h, -1, 0)
               + np.roll(np.roll(h, -1, 0), -1, 1))
              - (np.roll(np.roll(h, 1, 0), 1, 1)
                 + 2.0 * np.roll(h, 1, 0)
                 + np.roll(np.roll(h, 1, 0), -1, 1)))
        dx = -gx * float(strength) * 0.25
        dy = -gy * float(strength) * 0.25
        nz = np.ones_like(h)
        inv = 1.0 / np.sqrt(dx * dx + dy * dy + nz * nz)
        nrm = np.stack([dx * inv, dy * inv, nz * inv], axis=-1)
        return np.clip(nrm * 127.5 + 127.5, 0, 255).astype(np.uint8)

    @staticmethod
    def ao_from_height(height, strength=1.4, radii=(2, 4, 8)):
        """Baked AO (R channel, mostly bright) from a height field via
        multi-tap horizon sampling in 8 directions (periodic)."""
        h = height.astype(np.float64)
        occ = np.zeros_like(h)
        taps = 0
        for r in radii:
            r = max(1, int(r))
            for k in range(8):
                ang = k * math.pi / 4.0
                dx = int(round(math.cos(ang) * r))
                dy = int(round(math.sin(ang) * r))
                shifted = np.roll(np.roll(h, dy, 0), dx, 1)
                occ += np.maximum(shifted - h, 0.0) / float(r)
                taps += 1
        ao = 1.0 - float(strength) * occ / max(taps, 1) * 8.0
        return np.clip(ao, 0.0, 1.0)

# ---------------------------------------------------------------------------
#  PNG plumbing (pygame surface -> BytesIO, pure-zlib fallback; lazy, no PIL)
# ---------------------------------------------------------------------------

def _fp_png_from_rgb_bytes(w, h, raw):
    """Minimal pure-python PNG encoder (RGB8, filter 0).  Fallback path for
    environments where pygame.image.save cannot encode PNG; stdlib only."""
    import zlib
    stride = w * 3
    rows = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride]
                    for y in range(h))

    def _chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(rows, 6))
            + _chunk(b"IEND", b""))


def _fp_rgb_to_png(rgb):
    """uint8 (h, w, 3) array -> PNG bytes.  Primary path: pygame surface +
    pygame.image.save to a BytesIO; defensive fallbacks never raise."""
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    raw = rgb.tobytes()
    try:
        if not pygame.get_init():
            pygame.init()
        surf = pygame.Surface((w, h))
        pygame.surfarray.blit_array(surf, rgb.transpose(1, 0, 2))
        import io
        buf = io.BytesIO()
        try:
            pygame.image.save(surf, buf, "PNG")
        except TypeError:
            buf = io.BytesIO()
            pygame.image.save(surf, buf)
        data = buf.getvalue()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
    except Exception as ex:
        Log.warn(f"[FinalPatch] pygame PNG encode fell back to "
                 f"pure-python: {ex}")
    return _fp_png_from_rgb_bytes(w, h, raw)


def _fp_gray_to_png(field):
    v = np.clip(field * 255.0, 0, 255).astype(np.uint8)
    return _fp_rgb_to_png(np.stack([v, v, v], axis=-1))


# ---------------------------------------------------------------------------
#  Per-biome AAA material pipelines.  Each returns a dict of float maps:
#  albedo (h,w,3), height, rough, metal, ao (h,w), emissive (h,w,3) or None.
#  Every ingredient is periodic => every map is perfectly tileable.
# ---------------------------------------------------------------------------

def _fp_maps_military(size, seed, mc):
    """Scratched painted metal with Worley wear chips."""
    noise = _fp_tex.fbm(size, 4, 4, seed)
    f1, f2 = _fp_tex.worley(size, 16, seed + 5, 0.9)
    chips = (f1 < 0.16).astype(np.float64)            # paint worn to metal
    ridg = _fp_tex.ridged(size, 16, 2, seed + 9)
    scratch = (ridg > 0.93).astype(np.float64)        # thin scratch lines
    base = np.array(mc["base"][:3], dtype=np.float64)
    albedo = base[None, None, :] * (0.80 + 0.40 * noise)[..., None]
    bare = np.array((0.46, 0.47, 0.49), dtype=np.float64)
    albedo = (albedo * (1.0 - chips[..., None] * 0.85)
              + bare[None, None, :] * chips[..., None] * 0.85)
    albedo = albedo * (1.0 + 0.25 * scratch[..., None])
    grime = _fp_tex.fbm(size, 3, 3, seed + 21)
    albedo = albedo * (1.0 - 0.35 * grime[..., None])
    height = noise * 0.55 - chips * 0.30 + scratch * 0.12 - grime * 0.10
    rough = np.clip(0.48 + 0.25 * noise - 0.18 * chips
                    - 0.10 * scratch, 0.0, 1.0)
    metal = np.clip(0.55 + 0.35 * chips - 0.30 * grime, 0.0, 1.0)
    return albedo, height, rough, metal, None


def _fp_maps_office(size, seed, mc):
    """Drywall + low-pile carpet feel: soft FBM, faint panel joints."""
    noise = _fp_tex.fbm(size, 6, 4, seed)
    f1, f2 = _fp_tex.worley(size, 4, seed + 3, 0.0)   # regular panel grid
    joint = ((f2 - f1) < 0.035).astype(np.float64)    # F2-F1 seams
    fiber = _fp_tex.gradient(size, 64, seed + 11)     # carpet fiber
    base = np.array(mc["base"][:3], dtype=np.float64)
    albedo = base[None, None, :] * (0.90 + 0.16 * noise
                                    + 0.06 * fiber)[..., None]
    albedo = albedo * (1.0 - 0.25 * joint[..., None])
    height = noise * 0.35 + fiber * 0.20 - joint * 0.25
    rough = np.clip(0.82 + 0.12 * noise + 0.06 * joint - 0.08 * fiber,
                    0.0, 1.0)
    metal = np.zeros((size, size), dtype=np.float64)
    return albedo, height, rough, metal, None


def _fp_maps_scifi(size, seed, mc):
    """Hex-panel plating from cellular distance fields + emissive strips."""
    f1, f2 = _fp_tex.worley(size, 6, seed + 7, 0.22)
    edge = f2 - f1                                    # cellular distance
    seam = (edge < 0.055).astype(np.float64)          # panel seams
    band = ((edge >= 0.055) & (edge < 0.10)).astype(np.float64)
    noise = _fp_tex.fbm(size, 8, 3, seed + 13)
    base = np.array(mc["base"][:3], dtype=np.float64)
    panel = np.array(mc["panel"][:3], dtype=np.float64)
    bevel = np.clip(1.0 - f1 * 0.55, 0.0, 1.0)        # raised cell centre
    albedo = (base[None, None, :] * (0.75 + 0.45 * bevel * noise)[..., None])
    albedo = (albedo * (1.0 - seam[..., None])
              + panel[None, None, :] * seam[..., None] * 0.7)
    height = bevel * 0.5 - seam * 0.45 + noise * 0.15
    rough = np.clip(0.30 + 0.20 * seam + 0.15 * noise - 0.10 * bevel,
                    0.0, 1.0)
    metal = np.clip(0.85 - 0.15 * seam, 0.0, 1.0)
    ec = np.array(mc.get("emissive") or (0, 0, 0), dtype=np.float64)
    glow = (0.55 + 0.45 * noise)[..., None]
    emissive = band[..., None] * ec[None, None, :] * glow
    return albedo, height, rough, metal, emissive


def _fp_maps_lab(size, seed, mc):
    """Seamless cleanroom tile with F2-F1 grout lines."""
    f1, f2 = _fp_tex.worley(size, 8, seed + 2, 0.0)   # regular square grid
    grout = ((f2 - f1) < 0.045).astype(np.float64)
    noise = _fp_tex.fbm(size, 8, 3, seed + 17)
    base = np.array(mc["base"][:3], dtype=np.float64)
    albedo = base[None, None, :] * (0.96 + 0.06 * noise)[..., None]
    albedo = albedo * (1.0 - 0.40 * grout[..., None])
    height = noise * 0.18 - grout * 0.35
    rough = np.clip(0.34 + 0.10 * noise + 0.35 * grout, 0.0, 1.0)
    metal = np.full((size, size), 0.08, dtype=np.float64) * (1.0 - grout)
    ec = np.array(mc.get("emissive") or (0, 0, 0), dtype=np.float64)
    emissive = None
    if np.any(ec > 0.0):
        strip = (grout > 0) & (noise > 0.72)          # sparse status lights
        emissive = strip[..., None].astype(np.float64) * ec[None, None, :]
    return albedo, height, rough, metal, emissive


def _fp_maps_bunker(size, seed, mc):
    """Cast concrete: ridged multifractal pitting + warped-FBM water stains."""
    ridg = _fp_tex.ridged(size, 6, 4, seed)
    pits = np.clip((0.42 - ridg) * 3.0, 0.0, 1.0)     # air-pocket pitting
    noise = _fp_tex.fbm(size, 4, 3, seed + 29)
    warp = _fp_tex.warped_fbm(size, 3, 3, seed + 7, warp=6.0)
    # Vertical streaking: compress the warp field along y (water runs down).
    idx = (np.arange(size)[:, None] * 4) % size
    streak = warp[idx, np.arange(size)[None, :]]
    stain = np.clip((streak - 0.42) * 2.4, 0.0, 1.0)
    base = np.array(mc["base"][:3], dtype=np.float64)
    rust = np.array((0.32, 0.24, 0.16), dtype=np.float64)
    albedo = base[None, None, :] * (0.82 + 0.30 * noise)[..., None]
    albedo = (albedo * (1.0 - 0.55 * stain[..., None])
              + rust[None, None, :] * stain[..., None] * 0.45)
    albedo = albedo * (1.0 - 0.35 * pits[..., None])
    height = noise * 0.35 - pits * 0.50 - stain * 0.08
    rough = np.clip(0.90 + 0.10 * pits - 0.25 * stain, 0.0, 1.0)
    metal = np.zeros((size, size), dtype=np.float64)
    return albedo, height, rough, metal, None


def _fp_maps_street(size, seed, mc, part="wall"):
    """[FINALPATCH3:street_biome] Street biome per-part material maps.

    part == "wall" : running-bond brick — mortar grid via periodic row/column
                     arithmetic, per-brick tone jitter via _fp_hash_grid,
                     fbm grime bleeding up from the street line.
    part == "roof" : dirty plaster — warped-fbm damp stains + hairline cracks
                     (ridged) over a warm off-white plaster base.
    part == "floor": clean asphalt — dark bitumen base, fine Worley aggregate
                     speckle, subtle fbm patching, high roughness.
    """
    if part == "roof":
        warp = _fp_tex.warped_fbm(size, 3, 3, seed + 21, warp=5.0)
        stain = np.clip((warp - 0.52) * 2.2, 0.0, 1.0)
        crack = (_fp_tex.ridged(size, 10, 2, seed + 33) > 0.90).astype(np.float32)
        base = np.array(mc["base"][:3], dtype=np.float32)     # plaster white
        albedo = base[None, None, :] * (0.94 + 0.06 * warp[..., None])
        albedo = albedo * (1.0 - 0.38 * stain[..., None])
        albedo = albedo * (1.0 - 0.25 * crack[..., None])
        height = 0.55 + 0.10 * warp - 0.18 * crack - 0.10 * stain
        rough = np.clip(0.88 + 0.06 * warp + 0.05 * stain, 0.0, 1.0)
        metal = np.zeros((size, size), dtype=np.float32)
        return albedo, height, rough, metal, None
    if part == "floor":
        fbm = _fp_tex.fbm(size, 4, 4, seed + 41)
        f1, _f2 = _fp_tex.worley(size, 48, seed + 43, 0.9)
        agg = np.clip((0.20 - f1) * 6.0, 0.0, 1.0)            # stone speckle
        patch = np.clip((_fp_tex.fbm(size, 2, 2, seed + 47) - 0.55) * 2.0, 0.0, 1.0)
        base = np.array((0.16, 0.16, 0.17), dtype=np.float32)  # bitumen
        albedo = base[None, None, :] * (0.85 + 0.30 * fbm[..., None])
        albedo = albedo + agg[..., None] * 0.10                # bright aggregate
        albedo = albedo * (1.0 - 0.18 * patch[..., None])      # repair patches
        height = 0.45 + 0.12 * fbm + 0.22 * agg - 0.06 * patch
        rough = np.clip(0.93 + 0.04 * fbm - 0.10 * agg, 0.0, 1.0)
        metal = np.zeros((size, size), dtype=np.float32)
        return albedo, height, rough, metal, None
    # ---- wall: running-bond brick --------------------------------------
    rows, cols = 8, 4                       # brick courses per tile (divide size)
    if size % rows == 0 and size % cols == 0:
        rh, cw = size // rows, size // cols
        iy, ix = np.mgrid[0:size, 0:size]
        row = iy // rh
        shift = (row % 2) * (cw // 2)
        # % cols keeps the per-brick hash periodic across the horizontal
        # tile seam (col would otherwise reach `cols` on shifted rows).
        col = ((ix + shift) // cw) % cols
        mortar = ((iy % rh) < 2) | (((ix + shift) % cw) < 2)
        jit = _fp_hash_grid(row, col, seed + 7).astype(np.float32)
        brick = np.array((0.42, 0.20, 0.14), dtype=np.float32)   # fired clay
        joint = np.array((0.62, 0.60, 0.56), dtype=np.float32)   # pale mortar
        albedo = brick[None, None, :] * (0.80 + 0.40 * jit[..., None])
        grime = _fp_tex.fbm(size, 3, 3, seed + 9)
        albedo = albedo * (0.82 + 0.18 * grime[..., None])
        albedo = np.where(mortar[..., None], joint[None, None, :]
                          * (0.90 + 0.10 * grime[..., None]), albedo)
        height = np.where(mortar, 0.30 + 0.05 * jit,
                          0.60 + 0.08 * jit - 0.06 * grime)
        rough = np.clip(np.where(mortar, 0.90, 0.82) + 0.08 * grime, 0.0, 1.0)
        metal = np.zeros((size, size), dtype=np.float32)
        return albedo, height, rough, metal, None
    # size not divisible by the brick grid: a mortar lattice would not
    # tile, so fall back to a plain jittered brick tone (still periodic).
    jit = _fp_tex.fbm(size, 8, 2, seed + 7).astype(np.float32)
    grime = _fp_tex.fbm(size, 3, 3, seed + 9)
    brick = np.array((0.42, 0.20, 0.14), dtype=np.float32)
    albedo = brick[None, None, :] * (0.80 + 0.40 * jit[..., None])
    albedo = albedo * (0.82 + 0.18 * grime[..., None])
    height = 0.60 + 0.08 * jit - 0.06 * grime
    rough = np.clip(0.82 + 0.08 * grime, 0.0, 1.0)
    metal = np.zeros((size, size), dtype=np.float32)
    return albedo, height, rough, metal, None


_FP_STREET_PART_MATS = {
    "wall":  {"base": (0.48, 0.26, 0.18, 1.0), "metallic": 0.0, "roughness": 0.85},
    "roof":  {"base": (0.80, 0.78, 0.72, 1.0), "metallic": 0.0, "roughness": 0.90},
    "floor": {"base": (0.18, 0.18, 0.19, 1.0), "metallic": 0.0, "roughness": 0.95},
}


def _fp_module_part(key):
    """[FINALPATCH3:street_biome] classify a module key as wall/roof/floor."""
    if key in ("wall", "corner_column", "door_frame", "window_frame",
               "stairwell"):
        return "wall"
    if key == "ceiling":
        return "roof"
    return "floor"


_FP_BIOME_PIPELINES = {
    "military": _fp_maps_military,
    "office": _fp_maps_office,
    "sci-fi": _fp_maps_scifi,
    "lab": _fp_maps_lab,
    "bunker": _fp_maps_bunker,
    "street": _fp_maps_street,  # [FINALPATCH3:street_biome]
}


def _fp_biome_textures(biome, part=None):
    """Compose the biome's tileable base-color / normal / MR / AO / emissive
    PNG byte blobs from the raw feature maps.  Deterministic per biome."""
    cfg = BiomeManager.BIOMES.get(biome) or BiomeManager.BIOMES["military"]
    mc = cfg["materials"]
    size = _FP_TEX_SIZE
    seed = _fp_fnv1a("tex", biome)
    pipeline = _FP_BIOME_PIPELINES.get(biome, _fp_maps_military)
    if biome == "street":
        # [FINALPATCH3:street_biome] per-part maps (brick/plaster/asphalt);
        # same seed discipline as every other biome, so results stay
        # deterministic.  All non-street biomes take the original path.
        albedo, height, rough, metal, emissive = _fp_maps_street(
            size, seed, mc, part or "wall")
    else:
        albedo, height, rough, metal, emissive = pipeline(size, seed, mc)
    base_rgb = np.clip(albedo * 255.0, 0, 255).astype(np.uint8)
    normal_rgb = _fp_tex.normals_from_height(height, strength=2.2)
    ao = _fp_tex.ao_from_height(height, strength=1.4)
    mr_rgb = np.zeros((size, size, 3), dtype=np.uint8)
    mr_rgb[..., 1] = np.clip(rough * 255.0, 0, 255).astype(np.uint8)
    mr_rgb[..., 2] = np.clip(metal * 255.0, 0, 255).astype(np.uint8)
    emissive_rgb = None
    if emissive is not None:
        emissive_rgb = np.clip(emissive * 255.0, 0, 255).astype(np.uint8)
    return {
        "base": _fp_rgb_to_png(base_rgb),
        "normal": _fp_rgb_to_png(normal_rgb),
        "mr": _fp_rgb_to_png(mr_rgb),
        "emissive": (_fp_rgb_to_png(emissive_rgb)
                     if emissive_rgb is not None else None),
        "ao": _fp_gray_to_png(ao),
    }

# ---------------------------------------------------------------------------
#  _fp_mesh - high-quality procedural mesh builders (indexed arrays ->
#  Mesh.from_arrays; tangents via Mesh._compute_tangents at upload time).
#  Guarantees enforced in _fp_mesh.finalize(): no degenerate triangles,
#  outward winding (checked numerically per face), indices in range,
#  watertight where closed, zero internal/hidden faces by construction.
# ---------------------------------------------------------------------------

class _FPGeo:
    """Indexed triangle-soup accumulator with numerically verified outward
    winding and degenerate-face rejection."""

    __slots__ = ("pos", "nrm", "uv", "idx")

    def __init__(self):
        self.pos = []
        self.nrm = []
        self.uv = []
        self.idx = []

    def add_vertex(self, p, n, uv):
        self.pos.append((float(p[0]), float(p[1]), float(p[2])))
        self.nrm.append((float(n[0]), float(n[1]), float(n[2])))
        self.uv.append((float(uv[0]), float(uv[1])))
        return len(self.pos) - 1

    @staticmethod
    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def tri_checked(self, i0, i1, i2, normal):
        """Append triangle (i0,i1,i2) with CCW winding seen from `normal`;
        flips if needed, silently drops degenerate triangles."""
        a, b, c = self.pos[i0], self.pos[i1], self.pos[i2]
        e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        e2 = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        n = self._cross(e1, e2)
        area2 = n[0] * n[0] + n[1] * n[1] + n[2] * n[2]
        if area2 < 1e-16:
            return
        if (n[0] * normal[0] + n[1] * normal[1] + n[2] * normal[2]) < 0.0:
            i1, i2 = i2, i1
        self.idx.extend((i0, i1, i2))

    def quad(self, a, b, c, d, normal, uvs=None):
        """Append a (convex, planar) quad as 2 triangles with outward
        winding; planar UVs on the normal's dominant plane if not given."""
        e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        e2 = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        n = self._cross(e1, e2)
        if n[0] * n[0] + n[1] * n[1] + n[2] * n[2] < 1e-16:
            return                       # degenerate quad -> skip
        if (n[0] * normal[0] + n[1] * normal[1] + n[2] * normal[2]) < 0.0:
            b, d = d, b                  # reverse winding
            if uvs is not None:
                uvs = (uvs[0], uvs[3], uvs[2], uvs[1])
        if uvs is None:
            an = tuple(abs(v) for v in normal)
            pts = (a, b, c, d)
            if an[0] >= an[1] and an[0] >= an[2]:
                uvs = tuple((p[2], p[1]) for p in pts)
            elif an[1] >= an[2]:
                uvs = tuple((p[0], p[2]) for p in pts)
            else:
                uvs = tuple((p[0], p[1]) for p in pts)
            uvs = tuple((u * 0.5, v * 0.5) for (u, v) in uvs)
        i0 = self.add_vertex(a, normal, uvs[0])
        i1 = self.add_vertex(b, normal, uvs[1])
        i2 = self.add_vertex(c, normal, uvs[2])
        i3 = self.add_vertex(d, normal, uvs[3])
        self.idx.extend((i0, i1, i2, i0, i2, i3))

    def box(self, cx, cy, cz, sx, sy, sz, uv_scale=0.5):
        """Axis-aligned box, 6 verified-outward faces (12 tris)."""
        hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
        faces = (
            ((1.0, 0.0, 0.0), ((hx, -hy, -hz), (hx, hy, -hz),
                               (hx, hy, hz), (hx, -hy, hz))),
            ((-1.0, 0.0, 0.0), ((-hx, -hy, hz), (-hx, hy, hz),
                                (-hx, hy, -hz), (-hx, -hy, -hz))),
            ((0.0, 1.0, 0.0), ((-hx, hy, -hz), (-hx, hy, hz),
                               (hx, hy, hz), (hx, hy, -hz))),
            ((0.0, -1.0, 0.0), ((-hx, -hy, hz), (-hx, -hy, -hz),
                                (hx, -hy, -hz), (hx, -hy, hz))),
            ((0.0, 0.0, 1.0), ((-hx, -hy, hz), (hx, -hy, hz),
                               (hx, hy, hz), (-hx, hy, hz))),
            ((0.0, 0.0, -1.0), ((hx, -hy, -hz), (-hx, -hy, -hz),
                                (-hx, hy, -hz), (hx, hy, -hz))),
        )
        for nrm, corners in faces:
            w = [(p[0] + cx, p[1] + cy, p[2] + cz) for p in corners]
            self.quad(w[0], w[1], w[2], w[3], nrm)

    def arrays(self, weld=False):
        pos = np.asarray(self.pos, dtype=np.float32).reshape(-1, 3)
        nrm = np.asarray(self.nrm, dtype=np.float32).reshape(-1, 3)
        uv = np.asarray(self.uv, dtype=np.float32).reshape(-1, 2)
        idx = np.asarray(self.idx, dtype=np.uint32)
        if weld and len(pos):
            key = {}
            remap = np.arange(len(pos), dtype=np.uint32)
            keep = []
            for i in range(len(pos)):
                k = (tuple(np.round(pos[i], 5)),
                     tuple(np.round(nrm[i], 3)),
                     tuple(np.round(uv[i], 3)))
                j = key.get(k)
                if j is None:
                    key[k] = len(keep)
                    keep.append(i)
                    remap[i] = len(keep) - 1
                else:
                    remap[i] = j
            pos, nrm, uv = pos[keep], nrm[keep], uv[keep]
            idx = remap[idx]
        return pos, nrm, uv, idx


class _fp_mesh:
    """Namespace for the procedural geometry builders (module-style)."""

    UV_SCALE = 0.5

    # -- finalize: integrity assertions ----------------------------------
    @staticmethod
    def finalize(geo, weld=False):
        pos, nrm, uv, idx = geo.arrays(weld=weld)
        if len(idx):
            assert int(idx.max()) < len(pos), "index out of range"
            p0 = pos[idx[0::3]].astype(np.float64)
            p1 = pos[idx[1::3]].astype(np.float64)
            p2 = pos[idx[2::3]].astype(np.float64)
            cr = np.cross(p1 - p0, p2 - p1)
            area = 0.5 * np.sqrt((cr * cr).sum(axis=1))
            assert float(area.min()) > 1e-9, "degenerate triangle detected"
            # winding agrees with the stored vertex normals (sampled)
            tri_n = cr / np.maximum(np.sqrt((cr * cr).sum(axis=1,
                                                          keepdims=True)),
                                    1e-12)
            vn = nrm[idx[0::3]].astype(np.float64)
            agree = (tri_n * vn).sum(axis=1)
            assert float(agree.min()) > 0.5, "inward-wound triangle detected"
        return pos, nrm, uv, idx

    # -- generic profile sweep -------------------------------------------
    @staticmethod
    def _sweep(geo, profile, axis, lo, hi, uv_scale):
        """Sweep a closed 2D profile along an axis.
        axis "x": profile (u, v) = (z, y), swept along x in [lo, hi]
        axis "y": profile (u, v) = (x, z), swept along y
        axis "z": profile (u, v) = (x, y), swept along z
        Outward normals are derived from the polygon orientation and
        verified numerically against the polygon centroid."""
        n = len(profile)
        area = 0.0
        for i in range(n):
            u0, v0 = profile[i]
            u1, v1 = profile[(i + 1) % n]
            area += u0 * v1 - u1 * v0
        cw = area < 0.0
        cu = sum(p[0] for p in profile) / n
        cv = sum(p[1] for p in profile) / n

        def p3(u, v, w):
            if axis == "x":
                return (w, v, u)
            if axis == "y":
                return (u, w, v)
            return (u, v, w)

        def n3(ou, ov):
            if axis == "x":
                return (0.0, ov, ou)
            if axis == "y":
                return (ou, 0.0, ov)
            return (ou, ov, 0.0)

        arc = 0.0
        for i in range(n):
            u0, v0 = profile[i]
            u1, v1 = profile[(i + 1) % n]
            du, dv = u1 - u0, v1 - v0
            elen = math.hypot(du, dv)
            if elen < 1e-9:
                continue
            if cw:
                ou, ov = -dv / elen, du / elen
            else:
                ou, ov = dv / elen, -du / elen
            # numeric verification against the polygon centroid
            mx, my = (u0 + u1) * 0.5, (v0 + v1) * 0.5
            if ou * (mx - cu) + ov * (my - cv) < 0.0:
                ou, ov = -ou, -ov
            nrm = n3(ou, ov)
            uvs = ((lo * uv_scale, arc * uv_scale),
                   (lo * uv_scale, (arc + elen) * uv_scale),
                   (hi * uv_scale, (arc + elen) * uv_scale),
                   (hi * uv_scale, arc * uv_scale))
            geo.quad(p3(u0, v0, lo), p3(u1, v1, lo),
                     p3(u1, v1, hi), p3(u0, v0, hi), nrm, uvs)
            arc += elen
        # end caps: fan around the profile centroid (star-shaped profiles)
        for cap_lo in (True, False):
            w = lo if cap_lo else hi
            if axis == "x":
                nrm = (-1.0, 0.0, 0.0) if cap_lo else (1.0, 0.0, 0.0)
            elif axis == "y":
                nrm = (0.0, -1.0, 0.0) if cap_lo else (0.0, 1.0, 0.0)
            else:
                nrm = (0.0, 0.0, -1.0) if cap_lo else (0.0, 0.0, 1.0)
            ci = geo.add_vertex(p3(cu, cv, w), nrm,
                                (cu * uv_scale, cv * uv_scale))
            ring = [geo.add_vertex(p3(u, v, w), nrm, (u * uv_scale,
                                                      v * uv_scale))
                    for (u, v) in profile]
            for i in range(n):
                geo.tri_checked(ci, ring[i], ring[(i + 1) % n], nrm)

    # -- parametric staircase --------------------------------------------
    @staticmethod
    def stair_metrics(cell, fh):
        """(n_steps, rise, tread) from rise/run math with Blondel's rule
        clamp (0.56 <= 2r + t <= 0.68).  Single source of truth shared by
        the mesh builder and the collider planner (the landing-slab notch
        above a stairwell is exactly one tread deep)."""
        run = float(cell)
        rise = float(fh)
        n = int(round((2.0 * rise + run) / 0.63))
        n = max(4, min(20, n))
        for _ in range(8):                       # Blondel clamp
            g = (2.0 * rise + run) / n
            if 0.56 <= g <= 0.68:
                break
            n += 1 if g > 0.68 else -1   # too steep -> more steps
            n = max(4, min(20, n))
        return n, rise / n, run / n

    @staticmethod
    def build_staircase(cell, fh):
        """Solid staircase: risers/treads from rise/run math with Blondel's
        rule clamp (0.56 <= 2r + t <= 0.68), closed side stringers (the
        stepped side profile triangulated as ONE T-junction-free fan per
        side, with collinear profile vertices merged first so coplanar
        segments never waste triangles), a closed back wall at z=+run/2
        (the stairwell's rear face is exterior-visible), planar UV unwrap
        per part, welded vertices, and ZERO internal/hidden faces (no
        bottom - the solid sits on the floor).  Ascends along +Z."""
        uv_scale = _fp_mesh.UV_SCALE
        run = float(cell)
        rise = float(fh)
        n, r, t = _fp_mesh.stair_metrics(cell, fh)
        hw = float(cell) * 0.5
        ys = [i * r for i in range(n + 1)]
        zs = [-run * 0.5 + i * t for i in range(n + 1)]
        geo = _FPGeo()
        for i in range(1, n + 1):
            geo.quad((-hw, ys[i], zs[i - 1]), (hw, ys[i], zs[i - 1]),
                     (hw, ys[i], zs[i]), (-hw, ys[i], zs[i]),
                     (0.0, 1.0, 0.0))                       # tread
            geo.quad((-hw, ys[i - 1], zs[i - 1]),
                     (-hw, ys[i], zs[i - 1]),
                     (hw, ys[i], zs[i - 1]),
                     (hw, ys[i - 1], zs[i - 1]),
                     (0.0, 0.0, -1.0))                      # riser
        # Back wall (z = +run/2): closes the stairwell's rear surface.
        geo.quad((-hw, 0.0, zs[n]), (hw, 0.0, zs[n]),
                 (hw, ys[n], zs[n]), (-hw, ys[n], zs[n]),
                 (0.0, 0.0, 1.0))
        # Stepped side profile (z, y): C0, A1, B1, ..., An, Bn, Cn
        poly = [(zs[0], 0.0)]
        for i in range(1, n + 1):
            poly.append((zs[i - 1], ys[i]))
            poly.append((zs[i], ys[i]))
        poly.append((zs[n], 0.0))
        # Merge coplanar (collinear) profile runs: a fan over a polygon
        # needs exactly (verts - 2) triangles, so dropping collinear
        # vertices is the only silhouette-preserving reduction possible.
        merged = [poly[0]]
        for k in range(1, len(poly) - 1):
            ax, ay = merged[-1]
            bx, by = poly[k]
            cx, cy = poly[k + 1]
            cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
            if abs(cross) > 1e-12:
                merged.append((bx, by))     # else coplanar: skip (merge)
        merged.append(poly[-1])
        poly = merged
        m = len(poly)
        for side in (-1.0, 1.0):
            x = side * hw
            nrm = (side, 0.0, 0.0)
            ring = [geo.add_vertex((x, y, z), nrm,
                                   (z * uv_scale, y * uv_scale))
                    for (z, y) in poly]
            fan = ring[-1]            # C_n sees the whole monotone polygon
            for k in range(m - 2):
                geo.tri_checked(fan, ring[k], ring[k + 1], nrm)
        return _fp_mesh.finalize(geo, weld=True)

    # -- profile-swept wall (baseboard -> field -> crown trim) ------------
    @staticmethod
    def build_wall(cell, fh):
        """Wall swept from a 2D cross-section profile along the wall length:
        baseboard kick -> flat field -> chamfered crown trim.  Closed end
        caps (centroid fan, no T-junctions, no degenerate tris), correct
        outward normals including the trim chamfers (verified numerically),
        continuous arc-length UV unwrap.  Hugs the cell's -Z edge."""
        t = _FP_WALL_T
        zb = -float(cell) * 0.5
        zf = zb + t
        profile = [                     # (z, y) cross-section
            (zb, 0.0),                  # back bottom
            (zb, fh),                   # back top
            (zf + 0.035, fh),           # crown lip
            (zf, fh - 0.045),           # crown chamfer
            (zf, 0.16),                 # field
            (zf + 0.028, 0.12),         # baseboard chamfer
            (zf + 0.028, 0.0),          # baseboard toe
        ]
        geo = _FPGeo()
        _fp_mesh._sweep(geo, profile, "x",
                        -float(cell) * 0.5, float(cell) * 0.5,
                        _fp_mesh.UV_SCALE)
        return _fp_mesh.finalize(geo)

    # -- corner column (mitered profile turn) ------------------------------
    @staticmethod
    def build_corner_column(cell, fh):
        """Square corner post with a 45-degree mitered corner (where two
        wall runs turn), swept vertically, both end caps closed."""
        s = 0.24
        m = 0.07
        x0 = -float(cell) * 0.5
        z0 = -float(cell) * 0.5
        profile = [                     # (x, z) footprint
            (x0, z0),
            (x0 + s, z0),
            (x0 + s, z0 + s - m),       # miter start
            (x0 + s - m, z0 + s),       # miter end
            (x0, z0 + s),
        ]
        geo = _FPGeo()
        _fp_mesh._sweep(geo, profile, "y", 0.0, float(fh),
                        _fp_mesh.UV_SCALE)
        return _fp_mesh.finalize(geo)

    # -- floor slab / ceiling with recessed edge ---------------------------
    @staticmethod
    def _slab_into(geo, cell, y_face, thickness=0.12, recess=0.02,
                   flip=False):
        """Slab with a recessed edge band (shadow reveal between modules).
        flip=False: visible face on TOP at y_face, body below (floor).
        flip=True: visible face on the UNDERSIDE at y_face (ceiling)."""
        c = float(cell)
        r = float(recess)
        if not flip:
            y0, y1, y2 = y_face, y_face - r, y_face - thickness
        else:
            y0, y1, y2 = y_face, y_face + r, y_face + thickness
        profile = [                     # (x, y) cross-section
            (-c / 2 + r, y2), (c / 2 - r, y2),
            (c / 2 - r, y1), (c / 2, y1),
            (c / 2, y0), (-c / 2, y0),
            (-c / 2, y1), (-c / 2 + r, y1),
        ]
        _fp_mesh._sweep(geo, profile, "z", -c / 2, c / 2, _fp_mesh.UV_SCALE)

    @staticmethod
    def build_floor(cell, fh):
        geo = _FPGeo()
        _fp_mesh._slab_into(geo, cell, 0.0)
        return _fp_mesh.finalize(geo)

    @staticmethod
    def build_ceiling(cell, fh):
        geo = _FPGeo()
        _fp_mesh._slab_into(geo, cell, float(fh), flip=True)
        return _fp_mesh.finalize(geo)

    # -- door / window frames (rectangular ring, zero hidden faces) -------
    @staticmethod
    def _frame_into(geo, cell, fh, hx0, hx1, hy0, hy1):
        """Wall panel with a rectangular hole hugging the cell's -Z edge:
        front ring + back ring + hole reveal + outer sides; bottom face
        omitted (floor contact).  Strips tile the ring exactly - no
        internal faces."""
        c = float(cell)
        fh = float(fh)
        t = _FP_WALL_T
        zb = -c * 0.5
        zf = zb + t
        x0, x1 = -c * 0.5, c * 0.5
        strips = (
            (x0, 0.0, hx0, fh),               # left full-height
            (hx1, 0.0, x1, fh),               # right full-height
            (hx0, hy1, hx1, fh),              # above the hole
            (hx0, 0.0, hx1, hy0),             # below the hole (windows)
        )
        for (sx0, sy0, sx1, sy1) in strips:
            if sx1 - sx0 < 1e-6 or sy1 - sy0 < 1e-6:
                continue
            for z, nz in ((zf, 1.0), (zb, -1.0)):
                geo.quad((sx0, sy0, z), (sx1, sy0, z),
                         (sx1, sy1, z), (sx0, sy1, z),
                         (0.0, 0.0, nz))
        # hole reveal (jamb depth faces)
        geo.quad((hx0, hy0, zf), (hx0, hy1, zf),
                 (hx0, hy1, zb), (hx0, hy0, zb), (1.0, 0.0, 0.0))
        geo.quad((hx1, hy0, zb), (hx1, hy1, zb),
                 (hx1, hy1, zf), (hx1, hy0, zf), (-1.0, 0.0, 0.0))
        geo.quad((hx0, hy1, zb), (hx1, hy1, zb),
                 (hx1, hy1, zf), (hx0, hy1, zf), (0.0, -1.0, 0.0))
        if hy0 > 1e-6:                          # window sill top
            geo.quad((hx0, hy0, zf), (hx1, hy0, zf),
                     (hx1, hy0, zb), (hx0, hy0, zb), (0.0, 1.0, 0.0))
        # outer sides (bottom omitted: sits on the floor)
        geo.quad((x0, 0.0, zb), (x0, fh, zb),
                 (x0, fh, zf), (x0, 0.0, zf), (-1.0, 0.0, 0.0))
        geo.quad((x1, 0.0, zf), (x1, fh, zf),
                 (x1, fh, zb), (x1, 0.0, zb), (1.0, 0.0, 0.0))
        geo.quad((x0, fh, zf), (x1, fh, zf),
                 (x1, fh, zb), (x0, fh, zb), (0.0, 1.0, 0.0))

    @staticmethod
    def build_door_frame(cell, fh):
        """Door frame with lintel: 2.1 m clear opening, jamb reveals."""
        c = float(cell)
        jw = 0.18
        geo = _FPGeo()
        _fp_mesh._frame_into(geo, c, fh, -(c / 2 - jw), c / 2 - jw,
                             0.0, 2.1)
        return _fp_mesh.finalize(geo)

    @staticmethod
    def build_window_frame(cell, fh):
        """Window frame: 1.0..2.2 m opening with sill; the thin glass pane
        is a SEPARATE mesh (build_glass_pane) with its own MASK material."""
        c = float(cell)
        sw = 0.30
        geo = _FPGeo()
        _fp_mesh._frame_into(geo, c, fh, -(c / 2 - sw), c / 2 - sw,
                             1.0, 2.2)
        return _fp_mesh.finalize(geo)

    @staticmethod
    def build_glass_pane(cell, fh):
        """Thin double-faced glass pane filling the window opening
        (separate MASK-material slot)."""
        c = float(cell)
        sw = 0.30
        t = _FP_WALL_T
        zm = -c * 0.5 + t * 0.5
        gx0, gx1 = -(c / 2 - sw) + 0.01, (c / 2 - sw) - 0.01
        gy0, gy1 = 1.01, 2.19
        geo = _FPGeo()
        geo.quad((gx0, gy0, zm + 0.005), (gx1, gy0, zm + 0.005),
                 (gx1, gy1, zm + 0.005), (gx0, gy1, zm + 0.005),
                 (0.0, 0.0, 1.0))
        geo.quad((gx0, gy1, zm - 0.005), (gx1, gy1, zm - 0.005),
                 (gx1, gy0, zm - 0.005), (gx0, gy0, zm - 0.005),
                 (0.0, 0.0, -1.0))
        return _fp_mesh.finalize(geo)

    # -- biome room specials (recessed slab base + prop boxes) -------------
    @staticmethod
    def _special(cell, fh, props):
        geo = _FPGeo()
        _fp_mesh._slab_into(geo, cell, 0.0)
        for (cx, cy, cz, sx, sy, sz) in props:
            geo.box(cx, cy, cz, sx, sy, sz)
        return _fp_mesh.finalize(geo)

    @staticmethod
    def build_room(cell, fh):
        c = float(cell)
        return _fp_mesh._special(cell, fh, [
            (0.0, 0.017, 0.0, c * 0.62, 0.03, c * 0.62)])     # inset rug

    @staticmethod
    def build_corridor(cell, fh):
        return _fp_mesh._special(cell, fh, [
            (0.0, fh - 0.10, 0.0, float(cell), 0.08, 0.30)])  # accent trim

    @staticmethod
    def build_lobby(cell, fh):
        c = float(cell)
        return _fp_mesh._special(cell, fh, [
            (0.0, 0.019, 0.0, c * 0.40, 0.035, c * 0.40),     # medallion
            (0.0, fh - 0.12, 0.0, c * 0.7, 0.06, c * 0.7)])   # cove trim

    @staticmethod
    def build_bunker_cell(cell, fh):
        c = float(cell)
        return _fp_mesh._special(cell, fh, [
            (0.0, 1.102, -c * 0.5 + 0.04, c * 0.9, 2.2, 0.08)])  # back panel

    @staticmethod
    def build_lab_cleanroom(cell, fh):
        c = float(cell)
        return _fp_mesh._special(cell, fh, [
            (0.0, fh - 0.04, 0.0, c * 0.6, 0.06, c * 0.35)])  # light panel

    @staticmethod
    def build_office_cubicle(cell, fh):
        c = float(cell)
        return _fp_mesh._special(cell, fh, [
            (0.0, 0.702, -c * 0.25, c * 0.9, 1.4, 0.06),      # partition N
            (-c * 0.25, 0.702, c * 0.1, 0.06, 1.4, c * 0.55)])  # part. W

    @staticmethod
    def build_sci_hex_corridor(cell, fh):
        """Sci-fi hex corridor: recessed floor slab + two angled wall
        cheeks (verified outward normals, forming the hex profile)."""
        c = float(cell)
        geo = _FPGeo()
        _fp_mesh._slab_into(geo, cell, 0.0)
        tilt = 0.42
        st, ct = math.sin(tilt), math.cos(tilt)
        for zside, sgn in ((-c * 0.42, 1.0), (c * 0.42, -1.0)):
            # In-plane "up along the cheek" axis: each cheek leans toward
            # the corridor centre as it rises (hexagonal cross-section).
            vy, vz = ct, sgn * st
            # Corridor-facing normal (tilts down toward the viewer, like
            # the face of a lean-in wall); quad() fixes winding to match.
            nrm = (0.0, -st, sgn * ct)
            cy = fh * 0.45
            half_h = fh * 0.45
            half_w = c * 0.5
            corners = []
            for du, dv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                corners.append((du * half_w,
                                cy + dv * half_h * vy,
                                zside + dv * half_h * vz))
            geo.quad(corners[0], corners[1], corners[2], corners[3], nrm)
        return _fp_mesh.finalize(geo)


_FP_BUILDERS = {
    "floor": _fp_mesh.build_floor,
    "ceiling": _fp_mesh.build_ceiling,
    "wall": _fp_mesh.build_wall,
    "corner_column": _fp_mesh.build_corner_column,
    "door_frame": _fp_mesh.build_door_frame,
    "window_frame": _fp_mesh.build_window_frame,
    "glass_pane": _fp_mesh.build_glass_pane,
    "stairwell": _fp_mesh.build_staircase,
    "corridor_x": _fp_mesh.build_corridor,
    "room_2x2": _fp_mesh.build_room,
    "lobby": _fp_mesh.build_lobby,
    "bunker_cell": _fp_mesh.build_bunker_cell,
    "lab_cleanroom": _fp_mesh.build_lab_cleanroom,
    "office_cubicle": _fp_mesh.build_office_cubicle,
    "sci_hex_corridor": _fp_mesh.build_sci_hex_corridor,
}


def _fp_default_collider(key, cell, fh):
    """Default (local-space) collider plan for a module: a BoxCollider (or,
    for stairwells, the engine's native MeshCollider) plus the body rule.
    Static architecture gets NO RigidBody (static default), doors are
    kinematic, windows are STATIC thin boxes (frame and glass tile the
    window plane in z so their boxes touch but never interpenetrate),
    stairwells collide with their EXACT triangles (real risers + treads)."""
    t = _FP_WALL_T
    ze = -cell * 0.5 + t * 0.5
    if key == "wall":
        return {"c": [0.0, fh * 0.5, ze], "s": [cell, fh, t],
                "body": "static"}
    if key == "corner_column":
        s = 0.24
        return {"c": [-cell * 0.5 + s * 0.5, fh * 0.5, -cell * 0.5 + s * 0.5],
                "s": [s, fh, s], "body": "static"}
    if key == "door_frame":
        return {"c": [0.0, fh * 0.5, ze], "s": [cell, fh, t],
                "body": "kinematic"}
    if key == "window_frame":
        # STATIC thin box, z in [ze-0.03, ze+0.01] - the glass box takes
        # the adjacent slice [ze+0.01, ze+0.03], so the two tile the same
        # 0.06 m window plane with zero interpenetration.  NO RigidBody.
        return {"c": [0.0, 1.6, ze - 0.01], "s": [cell - 0.6, 1.2, 0.04],
                "body": "static"}
    if key == "glass_pane":
        # STATIC thin box on the glass (matches build_glass_pane's
        # 1.38 m x 1.18 m pane), z-adjacent to the frame box.  NO RigidBody.
        return {"c": [0.0, 1.6, ze + 0.02], "s": [cell - 0.62, 1.18, 0.02],
                "body": "static"}
    if key == "stairwell":
        # Exact-triangle collision: the engine's MeshCollider re-sources
        # triangles from the node's own shared Mesh at build time (the
        # same numpy arrays as the visual - never rebuilt), so the
        # collider is the real staircase (risers + treads), static.
        return {"kind": "mesh", "body": "static"}
    if key == "ceiling":
        return {"c": [0.0, fh + 0.06, 0.0], "s": [cell, 0.12, cell],
                "body": "static"}
    # All floor-bearing modules (floor / room / corridor / lobby / biome
    # specials) collide as one static floor slab.
    return {"c": [0.0, -0.06, 0.0], "s": [cell, 0.12, cell],
            "body": "static"}

# ---------------------------------------------------------------------------
#  A. BiomeManager - 5 biomes: modules, connection_rules, materials
# ---------------------------------------------------------------------------

class BiomeManager:
    """Static library of the 5 sector biomes.  Each entry carries:

      * modules           - module-key -> socket/role table used by the
                            solver and by the paint palette
      * connection_rules  - which module sockets may snap to which neighbours
                            (a "door_frame" only snaps between rooms of the
                            SAME biome; "window_frame" only faces
                            "__outside__"; cross-biome sockets never snap)
      * materials         - PBR configuration; the actual Material instances
                            (with embedded tileable PNG bytes from the
                            _fp_tex pipeline) are built lazily by
                            ProceduralSectorGenerator._material_for().

    Geometry for every module key is biome-independent; the (module_key,
    biome) mesh cache and the per-biome Material cache keep the renderer's
    (id(mesh), id(material)) instancing groups as large as possible.
    """

    _DOOR_OK = ("corridor_x", "room_2x2", "lobby", "office_cubicle",
                "lab_cleanroom", "bunker_cell", "sci_hex_corridor")

    _COMMON_MODULES = {
        "stairwell": {"socket": "stair"},
        "door_frame": {"socket": "door"},
        "window_frame": {"socket": "window"},
        "wall": {"socket": "wall"},
        "corner_column": {"socket": "column"},
        "floor": {"socket": "generic_floor"},
        "ceiling": {"socket": "generic_ceiling"},
    }

    @staticmethod
    def _modules(common, prefix, extra):
        mods = dict(common)
        for key, sock in extra.items():
            mods[key] = {"socket": f"{prefix}_{sock}"}
        return mods

    BIOMES = {
        "military": {
            "room_module": "bunker_cell",
            "corridor_module": "corridor_x",
            "modules": _modules(_COMMON_MODULES, "military", {
                "room_2x2": "room", "bunker_cell": "room",
                "corridor_x": "corridor"}),
            "connection_rules": {
                "door_frame": _DOOR_OK,
                "window_frame": ("__outside__",),
                "stairwell": ("corridor_x", "lobby", "room_2x2",
                              "bunker_cell"),
                "wall": ("wall", "door_frame", "window_frame",
                         "corner_column"),
            },
            "materials": {"base": (0.36, 0.38, 0.30, 1.0),
                          "panel": (0.22, 0.24, 0.19),
                          "metallic": 0.55, "roughness": 0.45,
                          "emissive": (0.0, 0.0, 0.0),
                          "emissive_intensity": 0.0,
                          "alpha_mode": "OPAQUE"},
        },
        "office": {
            "room_module": "office_cubicle",
            "corridor_module": "corridor_x",
            "modules": _modules(_COMMON_MODULES, "office", {
                "room_2x2": "room", "office_cubicle": "room",
                "corridor_x": "corridor"}),
            "connection_rules": {
                "door_frame": _DOOR_OK,
                "window_frame": ("__outside__",),
                "stairwell": ("corridor_x", "lobby", "room_2x2",
                              "office_cubicle"),
                "wall": ("wall", "door_frame", "window_frame",
                         "corner_column"),
            },
            "materials": {"base": (0.72, 0.70, 0.64, 1.0),
                          "panel": (0.55, 0.53, 0.47),
                          "metallic": 0.05, "roughness": 0.80,
                          "emissive": (0.0, 0.0, 0.0),
                          "emissive_intensity": 0.0,
                          "alpha_mode": "OPAQUE"},
        },
        "sci-fi": {
            "room_module": "room_2x2",
            "corridor_module": "sci_hex_corridor",
            "modules": _modules(_COMMON_MODULES, "sci", {
                "room_2x2": "room", "sci_hex_corridor": "hex"}),
            "connection_rules": {
                # sci_hex sockets never snap to drywall-style modules.
                "door_frame": ("sci_hex_corridor", "room_2x2", "lobby"),
                "window_frame": ("__outside__",),
                "stairwell": ("sci_hex_corridor", "lobby"),
                "wall": ("wall", "door_frame", "window_frame",
                         "corner_column"),
            },
            "materials": {"base": (0.16, 0.18, 0.23, 1.0),
                          "panel": (0.08, 0.10, 0.14),
                          "metallic": 0.85, "roughness": 0.30,
                          "emissive": (0.20, 0.85, 1.0),
                          "emissive_intensity": 3.0,
                          "alpha_mode": "OPAQUE"},
        },
        "lab": {
            "room_module": "lab_cleanroom",
            "corridor_module": "corridor_x",
            "modules": _modules(_COMMON_MODULES, "lab", {
                "room_2x2": "room", "lab_cleanroom": "room",
                "corridor_x": "corridor"}),
            "connection_rules": {
                "door_frame": _DOOR_OK,
                "window_frame": ("__outside__",),
                "stairwell": ("corridor_x", "lobby", "lab_cleanroom"),
                "wall": ("wall", "door_frame", "window_frame",
                         "corner_column"),
            },
            "materials": {"base": (0.86, 0.88, 0.89, 1.0),
                          "panel": (0.68, 0.72, 0.74),
                          "metallic": 0.10, "roughness": 0.35,
                          "emissive": (0.45, 1.0, 0.65),
                          "emissive_intensity": 1.6,
                          "alpha_mode": "OPAQUE"},
        },
        "bunker": {
            "room_module": "bunker_cell",
            "corridor_module": "corridor_x",
            "modules": _modules(_COMMON_MODULES, "bunker", {
                "room_2x2": "room", "bunker_cell": "room",
                "corridor_x": "corridor"}),
            "connection_rules": {
                "door_frame": _DOOR_OK,
                "window_frame": ("__outside__",),
                "stairwell": ("corridor_x", "lobby", "bunker_cell"),
                "wall": ("wall", "door_frame", "window_frame",
                         "corner_column"),
            },
            "materials": {"base": (0.46, 0.45, 0.42, 1.0),
                          "panel": (0.30, 0.30, 0.28),
                          "metallic": 0.02, "roughness": 0.92,
                          "emissive": (0.0, 0.0, 0.0),
                          "emissive_intensity": 0.0,
                          "alpha_mode": "OPAQUE"},
        },
        # [FINALPATCH3:street_biome] urban streetscape: brick walls, dirty
        # plaster roof, asphalt floors. Geometry reuses the standard module
        # set (per-part materials come from _fp_maps_street).
        "street": {
            "room_module": "room_2x2",
            "corridor_module": "corridor_x",
            "modules": _modules(_COMMON_MODULES, "street", {
                "room_2x2": "room", "corridor_x": "corridor"}),
            "connection_rules": {
                "door_frame": _DOOR_OK,
                "window_frame": ("__outside__",),
                "stairwell": ("corridor_x", "room_2x2"),
                "wall": ("wall", "door_frame", "window_frame", "corner_column"),
            },
            "materials": {"base": (0.48, 0.26, 0.18, 1.0),
                          "panel": (0.30, 0.16, 0.11),
                          "metallic": 0.0, "roughness": 0.85,
                          "emissive": (0.0, 0.0, 0.0),
                          "emissive_intensity": 0.0,
                          "alpha_mode": "OPAQUE"},
        },
    }

    ORDER = ("military", "office", "sci-fi", "lab", "bunker", "street")  # [FINALPATCH3:street_biome]

    @classmethod
    def sockets_match(cls, biome_a, sock_a, biome_b, sock_b):
        """Socket compatibility: identical socket families snap only within
        the same biome; 'generic_*' sockets snap to anything."""
        if not sock_a or not sock_b:
            return False
        if sock_a == sock_b:
            return biome_a == biome_b
        return sock_a.startswith("generic_") or sock_b.startswith("generic_")


# ---------------------------------------------------------------------------
#  B. Grid solver (BSP-lite): rooms + corridor + stair shaft + auto-fill seal
# ---------------------------------------------------------------------------

def _fp_solve(seed, biome, floors, fw, fd):
    """Deterministically plot modules onto a 3D integer grid.

    Returns a list of placements: {"cell": (gx, gy, gz), "key", "rot"}.
      * every occupied cell gets a floor-bearing module (auto-fill),
      * the top floor gets ceiling modules (auto-fill seal),
      * perimeter walls seal every floor; windows/door openings follow the
        biome's connection_rules; corner columns mark the shell corners,
      * one stairwell shaft aligns vertically across floors with a single
        yaw-snapped (rotation_index * 90 deg) orientation,
      * interior BSP split walls get rule-checked door gaps where the
        corridor crosses them.
    """
    rng = random.Random(int(seed))
    cfg = BiomeManager.BIOMES.get(biome) or BiomeManager.BIOMES["military"]
    rules = cfg["connection_rules"]
    room_key = cfg["room_module"]
    corr_key = cfg["corridor_module"]
    cells = {}

    def add(gx, gy, gz, key, rot=0):
        cell = (int(gx), int(gy), int(gz))
        entry = (key, int(rot) % 4)
        lst = cells.setdefault(cell, [])
        if entry not in lst:
            lst.append(entry)

    sx = rng.choice([1, max(1, fw - 2)])           # stair shaft column
    sz = rng.choice([1, max(1, fd - 2)])
    stair_rot = rng.randrange(4)                   # one yaw for ALL floors
    corr_z = max(1, min(fd - 2, fd // 2))          # corridor row
    split_x = rng.randint(2, fw - 3) if fw >= 6 else None
    split_z = rng.randint(2, fd - 3) if fd >= 6 else None
    door_zx = rng.randrange(1, fd - 1) if fd >= 3 else None
    door_xz = rng.randrange(1, fw - 1) if fw >= 3 else None
    entrance_x = fw // 2

    def module_at(gx, gy, gz):
        lst = cells.get((gx, gy, gz))
        return lst[0][0] if lst else None

    for gy in range(floors):
        # --- rooms / corridors / stair shaft + floor+ceiling auto-fill ----
        for gx in range(fw):
            for gz in range(fd):
                if gx == sx and gz == sz and gy < floors - 1:
                    add(gx, gy, gz, "stairwell", stair_rot)
                    add(gx, gy, gz, "floor")
                elif gz == corr_z:
                    add(gx, gy, gz, corr_key, 0)
                else:
                    add(gx, gy, gz, room_key, 0)
                if gy == floors - 1:
                    add(gx, gy, gz, "ceiling")
        # --- corner columns at the four shell corners ---------------------
        for cgx, cgz, crot in ((0, 0, 0), (fw - 1, 0, 1),
                               (fw - 1, fd - 1, 2), (0, fd - 1, 3)):
            add(cgx, gy, cgz, "corner_column", crot)
        # --- perimeter walls (seal the shell; windows + one entrance) -----
        for gx in range(fw):
            for gz, rot in ((0, 0), (fd - 1, 2)):
                if gx == sx and gz == sz:
                    add(gx, gy, gz, "wall", rot)
                    continue
                if gy == 0 and rot == 2 and gx == entrance_x:
                    add(gx, gy, gz, "door_frame", rot)
                elif (gx + gy + rot) % 3 == 1:
                    add(gx, gy, gz, "window_frame", rot)
                else:
                    add(gx, gy, gz, "wall", rot)
        for gz in range(fd):
            for gx, rot in ((0, 1), (fw - 1, 3)):
                if gx == sx and gz == sz:
                    add(gx, gy, gz, "wall", rot)
                elif (gz + gy + rot) % 3 == 1:
                    add(gx, gy, gz, "window_frame", rot)
                else:
                    add(gx, gy, gz, "wall", rot)
        # --- interior BSP split walls with rule-checked door gaps ---------
        if split_x is not None:
            for gz in range(fd):
                if gz == corr_z:
                    add(split_x, gy, gz, "door_frame", 1)
                elif gz == door_zx:
                    west = module_at(split_x - 1, gy, gz) or "room_2x2"
                    east = module_at(split_x + 1, gy, gz) or "room_2x2"
                    allowed = rules.get("door_frame", ())
                    if west in allowed and east in allowed:
                        add(split_x, gy, gz, "door_frame", 1)
                    else:
                        add(split_x, gy, gz, "wall", 1)
                else:
                    add(split_x, gy, gz, "wall", 1)
        if split_z is not None:
            for gx in range(fw):
                if gx == door_xz:
                    north = module_at(gx, gy, split_z - 1) or "room_2x2"
                    south = module_at(gx, gy, split_z + 1) or "room_2x2"
                    allowed = rules.get("door_frame", ())
                    if north in allowed and south in allowed:
                        add(gx, gy, split_z, "door_frame", 0)
                    else:
                        add(gx, gy, split_z, "wall", 0)
                else:
                    add(gx, gy, split_z, "wall", 0)

    placements = []
    for cell in sorted(cells):
        for key, rot in cells[cell]:
            placements.append({"cell": cell, "key": key, "rot": rot})
    return placements

# ---------------------------------------------------------------------------
#  Ghost preview renderer (shared "line" shader + dynamic VBO, LightGizmos
#  pattern; editor-only, lazily built, never crashes the frame)
# ---------------------------------------------------------------------------

class _FPGhost:
    """Draws the snapped paint-ghost line box (green = free, red = taken)."""

    _MAX_VERTS = 256

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
            self._vbo = ctx.buffer(reserve=self._MAX_VERTS * 6 * 4,
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
            count = min(len(lines) // 6, self._MAX_VERTS)
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

    def release(self):
        try:
            if self._vao is not None:
                self._vao.release()
        except Exception:
            pass
        try:
            if self._vbo is not None:
                self._vbo.release()
        except Exception:
            pass
        self._vao = None
        self._vbo = None
        self._ctx = None


# ---------------------------------------------------------------------------
#  B/C. ProceduralSectorGenerator - generate_sector + rebuild_module +
#  FPS-Creator grid painting + occupancy + Gizmo manual-control sync
# ---------------------------------------------------------------------------

class ProceduralSectorGenerator:
    """Builds deterministic, fully-collidable, save/load-safe building
    sectors out of procedural grid modules, and lets the user paint them by
    hand on a 2.0 m grid with a snapped ghost preview.

    One Mesh per (module_key, biome) and one Material per biome are cached
    and shared by every instance, so the renderer's existing geometry_inst
    fast-path (SECTION 15b, grouping on (id(mesh), id(material))) instances
    them automatically; if that path is ever unavailable the nodes still
    render through the standard per-mesh path unchanged.
    """

    MODES = ("generate", "paint", "manual")

    def __init__(self, engine):
        self.engine = engine
        self._mesh_cache = {}       # (module_key, biome) -> Mesh (shared)
        self._material_cache = {}   # biome -> Material (shared)
        self._texture_cache = {}    # biome -> texture dict
        self._glass_material = None
        # 3D integer occupancy grid: (gx, gy, gz) -> {module_key: [nodes]}.
        # Grid coords are GLOBAL (derived from world position), so painted,
        # generated, dragged and reloaded modules all share one truth.
        self.occupancy = {}
        # BUILDING EDITOR panel state (editor only; harmless in runtime).
        self.panel_open = False
        self.mode = "generate"
        self.biome_index = 0
        self.paint_key = "wall"
        self.paint_rot = 0            # yaw index, 90-degree steps ([/])
        self.paint_floor = 0
        self.floors = 1
        self.footprint = [8, 8]
        self._foot_sizes = [(6, 6), (8, 8), (10, 10), (12, 12), (16, 12)]
        self.user_seed = 1337
        self._gen_count = 0
        self._ghost = None            # (gx, gy, gz, occupied)
        self._ghost_renderer = _FPGhost()
        self._raycast_hit_node = None  # last module hit by _paint_raycast

    # ------------------------------------------------------------------
    #  Lazy shared resources
    # ------------------------------------------------------------------
    def _material_for(self, biome, key=None):
        # [FINALPATCH3:street_biome] per-part materials: the street biome
        # renders walls as brick, ceilings as dirty plaster and floors as
        # asphalt; every other biome keeps its single shared material.
        part = _fp_module_part(key) if (key is not None and biome == "street") else None
        ckey = (biome, part)
        mat = self._material_cache.get(ckey)
        if mat is not None:
            return mat
        cfg = BiomeManager.BIOMES.get(biome) or BiomeManager.BIOMES["military"]
        mc = cfg["materials"]
        if biome == "street" and part is not None:
            mc = dict(mc)
            mc.update(_FP_STREET_PART_MATS.get(part, {}))
        tkey = (biome, part)
        tex = self._texture_cache.get(tkey)
        if tex is None:
            try:
                tex = _fp_biome_textures(biome, part)
            except Exception as ex:
                Log.error(f"[FinalPatch] texture generation failed for "
                          f"'{biome}': {ex}")
                tex = {"base": None, "normal": None, "mr": None,
                       "emissive": None, "ao": None}
            self._texture_cache[tkey] = tex
        mat = Material(
            name=f"fp_{biome}_{part or 'all'}_mat",
            base_color=tuple(mc["base"]),
            metallic=float(mc["metallic"]),
            roughness=float(mc["roughness"]),
            emissive=tuple(mc["emissive"]),
            emissive_intensity=float(mc["emissive_intensity"]),
            base_color_tex=("bytes", tex["base"]) if tex["base"] else None,
            normal_tex=("bytes", tex["normal"]) if tex["normal"] else None,
            metallic_roughness_tex=("bytes", tex["mr"]) if tex["mr"] else None,
            emissive_tex=("bytes", tex["emissive"]) if tex["emissive"] else None,
            occlusion_tex=("bytes", tex["ao"]) if tex["ao"] else None,
            alpha_mode=mc.get("alpha_mode", "OPAQUE"),
        )
        self._material_cache[ckey] = mat
        return mat

    def _glass_mat(self):
        """Shared MASK-material for window glass panes."""
        if self._glass_material is None:
            self._glass_material = Material(
                name="fp_glass_mat",
                base_color=(0.62, 0.78, 0.86, 0.55),
                metallic=0.1,
                roughness=0.08,
                alpha_mode="MASK",
                alpha_cutoff=0.5,
                double_sided=True,
            )
        return self._glass_material

    def _mesh_for(self, key, biome, rot=0):
        """Shared cached Mesh for (key, biome, rot).  `rot` is only ever
        non-zero for stairwells: their yaw is BAKED into one of four mesh
        variants (node rotation_euler.y stays 0).  Reason: the engine's
        MeshCollider world-BVH builder (_mesh_shape_view) transforms
        triangles by the TRANSPOSE of the node's rotation matrix, so a
        MeshCollider on a node yawed 90/270 deg collides MIRRORED
        relative to the rendered mesh (0/180 are self-inverse and
        unaffected).  Pre-rotating the shared arrays keeps the visual
        mesh and the exact-triangle collider identical for every
        rotation_index, with zero engine-side changes."""
        ck = (key, biome, int(rot) % 4)
        mesh = self._mesh_cache.get(ck)
        if mesh is not None:
            return mesh
        ctx = getattr(self.engine, "ctx", None)
        if ctx is None:
            return None
        builder = _FP_BUILDERS.get(key, _fp_mesh.build_wall)
        try:
            pos, nrm, uv, idx = builder(_FP_CELL, _FP_FLOOR_H)
            if int(rot) % 4:
                # Bake the yaw into the arrays (same Ry convention the
                # node transform would apply: x' = x c + z s,
                # z' = -x s + z c), about the cell-centre origin.
                th = math.radians((int(rot) % 4) * 90.0)
                c, s = math.cos(th), math.sin(th)
                p64 = pos.astype(np.float64)
                px = p64[:, 0] * c + p64[:, 2] * s
                pz = -p64[:, 0] * s + p64[:, 2] * c
                pos = np.stack([px, p64[:, 1], pz],
                               axis=1).astype(np.float32)
                n64 = nrm.astype(np.float64)
                nx = n64[:, 0] * c + n64[:, 2] * s
                nz = -n64[:, 0] * s + n64[:, 2] * c
                nrm = np.stack([nx, n64[:, 1], nz],
                               axis=1).astype(np.float32)
            # Proper tangents -> correct normal mapping in the GGX pass.
            tangents = Mesh._compute_tangents(pos, nrm, uv, idx)
            mesh = Mesh.from_arrays(ctx, pos, nrm, uv, idx,
                                    name=f"fp_{key}_{biome}_r{int(rot) % 4}",
                                    tangents=tangents)
        except Exception as ex:
            Log.error(f"[FinalPatch] mesh build failed for '{key}': {ex}")
            return None
        self._mesh_cache[ck] = mesh
        return mesh

    # ------------------------------------------------------------------
    #  Occupancy grid (3D integer cells, global/world-derived)
    # ------------------------------------------------------------------
    @staticmethod
    def _grid_of(wx, wy, wz):
        """World position (module cell CENTRE) -> global grid coords.
        Exact snap math: cell_pos = floor(p / cell) on x/z, nearest
        floor level on y."""
        gx = int(math.floor(wx / _FP_CELL + 1e-6))
        gz = int(math.floor(wz / _FP_CELL + 1e-6))
        gy = int(round(wy / _FP_FLOOR_H))
        return gx, gy, gz

    @staticmethod
    def _cell_world(gx, gy, gz):
        """Grid coords -> world position of the module cell centre:
        world = (cell_pos + 0.5 * footprint_offset) * cell."""
        return ((gx + 0.5) * _FP_CELL, gy * _FP_FLOOR_H,
                (gz + 0.5) * _FP_CELL)

    def _occ_claim(self, node, key, gx, gy, gz):
        slot = self.occupancy.setdefault((gx, gy, gz), {})
        lst = slot.setdefault(key, [])
        if node not in lst:
            lst.append(node)
        node.user_data["_fp_occ"] = (gx, gy, gz)

    def _occ_release(self, node):
        occ = node.user_data.get("_fp_occ")
        if not occ:
            return
        slot = self.occupancy.get(occ)
        if slot:
            for key in list(slot):
                lst = slot[key]
                if node in lst:
                    lst.remove(node)
                if not lst:
                    del slot[key]
            if not slot:
                del self.occupancy[occ]
        node.user_data.pop("_fp_occ", None)

    def _free_node(self, node):
        """Free occupancy for a node and every procedural descendant
        (called from the _delete_node wrapper and before re-syncs)."""
        for child in node.traverse():
            if getattr(child, "mesh_type", None) == "procedural_module":
                self._occ_release(child)
            elif "_fp_occ" in getattr(child, "user_data", {}):
                self._occ_release(child)

    # ------------------------------------------------------------------
    #  Collider plan helpers (3D integer occupancy + wall-run merging)
    # ------------------------------------------------------------------
    @staticmethod
    def _rotated(yaw_deg, lx, ly, lz):
        """Local offset -> world offset for a yaw-only node rotation."""
        r = math.radians(yaw_deg)
        c, s = math.cos(r), math.sin(r)
        return (lx * c + lz * s, ly, -lx * s + lz * c)

    def _apply_plan(self, node, plan):
        """Attach (or clear) the collider/rigidbody described by a plan.
        Static architecture keeps rigidbody == None (static by default).
        A {"kind": "mesh"} plan attaches the engine's native MeshCollider
        (exact triangles re-sourced from the node's own mesh at physics
        build time) - always static, never a RigidBody."""
        if not plan or plan.get("body") == "none":
            node.collider = None
            node.rigidbody = None
            return
        if plan.get("kind") == "mesh":
            node.collider = MeshCollider()
            node.rigidbody = None
            return
        c = plan["c"]
        s = plan["s"]
        node.collider = BoxCollider(
            size=(float(s[0]), float(s[1]), float(s[2])),
            center=(float(c[0]), float(c[1]), float(c[2])))
        body = plan.get("body", "static")
        if body == "kinematic":
            node.rigidbody = RigidBody(body_type="kinematic")
        elif body == "dynamic":
            node.rigidbody = RigidBody(body_type="dynamic",
                                       mass=float(plan.get("mass", 5.0)))
        else:
            node.rigidbody = None

    # ------------------------------------------------------------------
    #  Shared module spawner (generate / paint / rebuild all use this)
    # ------------------------------------------------------------------
    def _spawn_module(self, key, biome, gx, gy, gz, rot, seed, parent,
                      plan=None, register=True, local=(0.0, 0.0, 0.0)):
        """Create one module node at grid cell (gx, gy, gz).  `local` is a
        LOCAL offset used when parenting under another module (glass)."""
        wx, wy, wz = self._cell_world(gx, gy, gz)
        node = SceneNode(f"FP_{key}_{gx}_{gy}_{gz}", parent=parent)
        if getattr(parent, "mesh_type", None) == "procedural_module":
            # Child of another module (e.g. window glass): local offset.
            node.position = glm.vec3(float(local[0]), float(local[1]),
                                     float(local[2]))
        else:
            # scene root or a sector root (always at the world origin):
            # local == world.
            node.position = glm.vec3(wx, wy, wz)
        node.rotation_euler = glm.vec3(0.0, (rot % 4) * 90.0, 0.0)
        if key == "stairwell":
            # Stair yaw lives in the mesh VARIANT (see _mesh_for): the
            # node itself stays yaw-0 so the exact-triangle MeshCollider
            # matches the rendered stair for every rotation_index.
            node.rotation_euler = glm.vec3(0.0, 0.0, 0.0)
        node.scale = glm.vec3(1.0)
        if key == "glass_pane":
            node.mesh = self._mesh_for(key, "__glass__")
            node.material = self._glass_mat()
        else:
            node.mesh = self._mesh_for(
                key, biome, rot if key == "stairwell" else 0)
            node.material = self._material_for(biome, key)
        node.mesh_type = "procedural_module"
        node.mesh_params = {
            "biome": biome,
            "module_key": key,
            "grid_pos": [int(gx), int(gy), int(gz)],
            "seed": int(seed),
            "rotation_index": int(rot) % 4,
            "cell_size": _FP_CELL,
            "floor_height": _FP_FLOOR_H,
            "origin": [0.0, 0.0, 0.0],
            "collider": plan,
        }
        if plan is not None:
            self._apply_plan(node, plan)
        if register and key != "glass_pane":
            self._occ_claim(node, key, int(gx), int(gy), int(gz))
        return node

    # ------------------------------------------------------------------
    #  B. generate_sector
    # ------------------------------------------------------------------
    def generate_sector(self, origin, biome_key, floors=1, footprint=(8, 8)):
        engine = self.engine
        try:
            floors = max(1, int(floors))
            fw = max(4, int(footprint[0]))
            fd = max(4, int(footprint[1]))
        except Exception:
            floors, fw, fd = 1, 8, 8
        if biome_key not in BiomeManager.BIOMES:
            Log.warn(f"[FinalPatch] unknown biome '{biome_key}', "
                     f"falling back to 'military'")
            biome_key = "military"
        ox, oy, oz = float(origin[0]), float(origin[1]), float(origin[2])
        cell, fh = _FP_CELL, _FP_FLOOR_H
        seed = _fp_fnv1a(biome_key, fw, fd, floors, self.user_seed)
        placements = _fp_solve(seed, biome_key, floors, fw, fd)

        try:
            base = f"Sector_{biome_key}"
            namer = getattr(engine, "_unique_node_name", None)
            root_name = namer(base) if callable(namer) else base
        except Exception:
            root_name = f"Sector_{biome_key}"
        root = SceneNode(root_name, parent=engine.scene.root)

        t = _FP_WALL_T
        nodes = {}
        col_seen = set()     # quantised world-space collider dedupe

        # --- pass 1: create the nodes (occupancy prevents double placing) -
        for pl in placements:
            gx, gy, gz = pl["cell"]
            key, rot = pl["key"], pl["rot"]
            wx = ox + (gx + 0.5) * cell
            wy = oy + gy * fh
            wz = oz + (gz + 0.5) * cell
            Gx, Gy, Gz = self._grid_of(wx, wy, wz)
            slot = self.occupancy.get((Gx, Gy, Gz)) or {}
            if slot.get(key):
                # the plan's corner / shared walls: normal, not a problem
                Log.debug(f"[FinalPatch] cell {(Gx, Gy, Gz)} already holds "
                          f"'{key}' - skipping duplicate placement")
                continue
            node = SceneNode(f"FP_{key}_{Gx}_{Gy}_{Gz}", parent=root)
            node.position = glm.vec3(wx, wy, wz)
            node.rotation_euler = glm.vec3(0.0, rot * 90.0, 0.0)
            if key == "stairwell":
                # Stair yaw is baked into the mesh variant (see
                # _mesh_for); the node itself stays yaw-0 so the
                # exact-triangle MeshCollider matches the visual.
                node.rotation_euler = glm.vec3(0.0, 0.0, 0.0)
            node.scale = glm.vec3(1.0)
            node.mesh = self._mesh_for(
                key, biome_key, rot if key == "stairwell" else 0)
            node.material = self._material_for(biome_key, key)
            node.mesh_type = "procedural_module"
            node.mesh_params = {
                "biome": biome_key,
                "module_key": key,
                "grid_pos": [Gx, Gy, Gz],
                "seed": seed,
                "rotation_index": rot,
                "cell_size": cell,
                "floor_height": fh,
                "origin": [ox, oy, oz],
                "collider": None,            # filled in during pass 3
            }
            self._occ_claim(node, key, Gx, Gy, Gz)
            nodes[(Gx, Gy, Gz, key, rot)] = node
            # Window glass: separate MASK-material child module (static
            # thin box, z-adjacent to the frame's box - no RigidBody).
            if key == "window_frame":
                glass = self._spawn_module(
                    "glass_pane", biome_key, Gx, Gy, Gz, 0, seed,
                    parent=node,
                    plan=_fp_default_collider("glass_pane", cell, fh),
                    register=False)
                glass.name = f"__fp_glass_{Gx}_{Gy}_{Gz}"

        # --- collider overlap prevention (zero interpenetrating boxes) ---
        # slab_above: cells with a floor-bearing module ONE FLOOR UP.  The
        # module above hangs its 0.12 m slab into this cell's top band
        # ([(Gy+1)*fh - 0.12, (Gy+1)*fh]), so wall/door/stair boxes must
        # stop flush UNDER the slab they support instead of piercing it.
        # cross_west/cross_east: cells whose rot-0/2 wall-plane boxes share
        # the cell with a PERPENDICULAR wall-plane box (rot 1 = west edge
        # strip, rot 3 = east edge strip); the rot-0/2 boxes are shortened
        # by one wall thickness at the crossing end(s) so no two
        # BoxColliders interpenetrate.
        slab_above = set()
        cross_west = set()
        cross_east = set()
        stair_above = {}     # cell -> stair rot for cells ABOVE a stairwell
        _WALL_FAMILY = ("wall", "door_frame")
        _NO_SLAB = ("wall", "corner_column", "door_frame", "window_frame",
                    "glass_pane", "stairwell", "ceiling")
        for (Gx, Gy, Gz, key, rot) in nodes:
            if key not in _NO_SLAB:
                slab_above.add((Gx, Gy - 1, Gz))
            if key == "stairwell":
                stair_above[(Gx, Gy + 1, Gz)] = rot
            if key in _WALL_FAMILY:
                if rot == 1:
                    cross_west.add((Gx, Gy, Gz))
                elif rot == 3:
                    cross_east.add((Gx, Gy, Gz))
        # Cells holding BOTH wall-plane orientations (crossing walls).
        crossed = set()
        for (Gx, Gy, Gz, key, rot) in nodes:
            if (key in _WALL_FAMILY and rot in (0, 2)
                    and ((Gx, Gy, Gz) in cross_west
                         or (Gx, Gy, Gz) in cross_east)):
                crossed.add((Gx, Gy, Gz))

        # --- pass 2: collider plans with merged wall runs -----------------
        # Group plain walls into runs of adjacent cells (per floor, per
        # orientation, per fixed grid line) and give each run ONE larger
        # BoxCollider carried by the first node of the run.  Walls in
        # crossed cells stay OUT of runs (a merged box cannot be notched);
        # runs are also sub-split wherever the slab-above state changes so
        # every merged box keeps one uniform top height.
        wall_groups = {}
        for pl in placements:
            if pl["key"] != "wall":
                continue
            gx, gy, gz = pl["cell"]
            rot = pl["rot"]
            wx = ox + (gx + 0.5) * cell
            wy = oy + gy * fh
            wz = oz + (gz + 0.5) * cell
            Gx, Gy, Gz = self._grid_of(wx, wy, wz)
            if (Gx, Gy, Gz, "wall", rot) not in nodes:
                continue
            if (Gx, Gy, Gz) in crossed:
                continue
            if rot in (0, 2):
                wall_groups.setdefault((Gy, rot, Gz), []).append(Gx)
            else:
                wall_groups.setdefault((Gy, rot, Gx), []).append(Gz)

        merged_plan = {}     # occ key -> plan ("none" for absorbed members)
        ze = -cell * 0.5 + t * 0.5
        for (Gy, rot, fixed), runs in wall_groups.items():
            runs = sorted(set(runs))

            def _slab_state(v, rot=rot, Gy=Gy, fixed=fixed):
                if rot in (0, 2):
                    return (v, Gy, fixed) in slab_above
                return (fixed, Gy, v) in slab_above

            i = 0
            while i < len(runs):
                j = i
                while j + 1 < len(runs) and runs[j + 1] == runs[j] + 1:
                    j += 1
                # Sub-split the contiguous run into segments of uniform
                # slab-above state (a single box cannot have two tops).
                k = i
                while k <= j:
                    l = k
                    st = _slab_state(runs[k])
                    while l + 1 <= j and _slab_state(runs[l + 1]) == st:
                        l += 1
                    a, b = runs[k], runs[l]
                    n = b - a + 1
                    if rot in (0, 2):
                        first = (a, Gy, fixed, "wall", rot)
                        members = [(x, Gy, fixed, "wall", rot)
                                   for x in range(a + 1, b + 1)]
                    else:
                        first = (fixed, Gy, a, "wall", rot)
                        members = [(fixed, Gy, z, "wall", rot)
                                   for z in range(a + 1, b + 1)]
                    node = nodes.get(first)
                    if node is not None and n > 1:
                        # Merged box in the FIRST node's LOCAL frame: runs
                        # are sorted along the global axis, which maps to
                        # local +X for rot 0/3 and local -X for rot 1/2
                        # (yaw-snapped).  With a slab above, the box stops
                        # flush UNDER the slab it supports (top lowered by
                        # the 0.12 m slab thickness).
                        sign = 1.0 if rot in (0, 3) else -1.0
                        top_h = fh - (0.12 if st else 0.0)
                        merged_plan[first] = {
                            "c": [sign * (n - 1) * 0.5 * cell,
                                  top_h * 0.5, ze],
                            "s": [n * cell, top_h, t],
                            "body": "static",
                        }
                        for mk in members:
                            merged_plan[mk] = {"body": "none"}
                    k = l + 1
                i = j + 1

        # --- pass 3: apply plans (occupancy grid prevents duplicates) -----
        for occ_key, node in nodes.items():
            Gx, Gy, Gz, key, rot = occ_key
            plan = merged_plan.get(occ_key)
            if plan is None:
                plan = _fp_default_collider(key, cell, fh)
                # Stop flush UNDER an upper-floor slab (subtract the slab
                # thickness from the box top) and inset wall boxes that
                # cross a perpendicular wall plane in the same cell.
                # (Mesh-collider plans - stairwells - carry no "c"/"s"
                # box to trim: their exact triangles already are the
                # walkable shape.)
                if (key in ("wall", "door_frame", "stairwell")
                        and "c" in plan
                        and (Gx, Gy, Gz) in slab_above):
                    plan = dict(plan)
                    plan["c"] = [plan["c"][0], plan["c"][1] - 0.06,
                                 plan["c"][2]]
                    plan["s"] = [plan["s"][0], plan["s"][1] - 0.12,
                                 plan["s"][2]]
                if (key in ("wall", "door_frame") and rot in (0, 2)
                        and "c" in plan
                        and (Gx, Gy, Gz) in crossed):
                    plan = dict(plan)
                    cx, sx = plan["c"][0], plan["s"][0]
                    # local +X maps to world +X for rot 0, world -X for
                    # rot 2 (180-degree yaw) - trim the WORLD crossing end.
                    xdir = 1.0 if rot == 0 else -1.0
                    if (Gx, Gy, Gz) in cross_west:
                        cx += xdir * t * 0.5     # shorten at the west end
                        sx -= t
                    if (Gx, Gy, Gz) in cross_east:
                        cx -= xdir * t * 0.5     # shorten at the east end
                        sx -= t
                    plan["c"] = [cx, plan["c"][1], plan["c"][2]]
                    plan["s"] = [sx, plan["s"][1], plan["s"][2]]
                # Landing-slab notch (v3): a floor-bearing module DIRECTLY
                # above a stairwell stops one tread-depth short of the
                # stair's top edge, so the stair's exact-triangle
                # MeshCollider (risers + treads) tiles the landing flush
                # with ZERO interpenetration - the top tread itself covers
                # the notched strip at exactly the same height (both are
                # at gy*fh + fh).  The VISUAL slab mesh still spans the
                # full cell; only the collider box is notched, so walking
                # coverage is unchanged.  The stair ascends along its
                # local +Z: rot 0/1/2/3 puts the top edge at the world
                # +Z/+X/-Z/-X cell edge (slab modules are rot 0 here, so
                # the notch maps directly onto local axes).
                srot = stair_above.get((Gx, Gy, Gz))
                if (srot is not None and key not in _NO_SLAB
                        and rot == 0 and "c" in plan):
                    _sn, _sr, _st = _fp_mesh.stair_metrics(cell, fh)
                    plan = dict(plan)
                    c0, s0 = plan["c"], plan["s"]
                    if srot == 0:            # stair top at world +Z edge
                        plan["c"] = [c0[0], c0[1], c0[2] - _st * 0.5]
                        plan["s"] = [s0[0], s0[1], s0[2] - _st]
                    elif srot == 2:          # world -Z edge
                        plan["c"] = [c0[0], c0[1], c0[2] + _st * 0.5]
                        plan["s"] = [s0[0], s0[1], s0[2] - _st]
                    elif srot == 1:          # world +X edge
                        plan["c"] = [c0[0] - _st * 0.5, c0[1], c0[2]]
                        plan["s"] = [s0[0] - _st, s0[1], s0[2]]
                    else:                    # rot 3: world -X edge
                        plan["c"] = [c0[0] + _st * 0.5, c0[1], c0[2]]
                        plan["s"] = [s0[0] - _st, s0[1], s0[2]]
                # Quantised world-space dedupe: a cell that already carries
                # an identical collider skips the duplicate.  (Mesh plans
                # are never deduped - every stairwell keeps its own
                # MeshCollider on its shared mesh.)
                if "c" not in plan:
                    node.mesh_params["collider"] = plan
                    self._apply_plan(node, plan)
                    continue
                wc = self._rotated(rot * 90.0, *plan["c"])
                q = (round(node.position.x + wc[0], 2),
                     round(node.position.y + wc[1], 2),
                     round(node.position.z + wc[2], 2),
                     round(plan["s"][0], 2), round(plan["s"][1], 2),
                     round(plan["s"][2], 2), plan["body"])
                if q in col_seen:
                    plan = {"body": "none"}
                else:
                    col_seen.add(q)
            if key == "corner_column":
                # Solver-placed columns sit at shell corners where two
                # perimeter wall boxes already meet - keep them visual trim
                # so zero colliders overlap at spawn time.
                plan = {"body": "none"}
            node.mesh_params["collider"] = plan
            self._apply_plan(node, plan)

        # If the game is already running, register the new static geometry
        # with the physics world immediately (otherwise _begin_play's full
        # rebuild picks everything up automatically).
        if getattr(engine, "_playing", False):
            phys = getattr(engine, "physics", None)
            if phys is not None:
                for node in nodes.values():
                    if getattr(node, "collider", None) is not None:
                        try:
                            phys.add_node(node)
                        except Exception:
                            pass
        Log.info(f"[FinalPatch] sector '{root_name}' generated: biome="
                 f"{biome_key} floors={floors} footprint={fw}x{fd} "
                 f"modules={len(nodes)} seed={seed}")
        return root

    # ------------------------------------------------------------------
    #  C. rebuild_module (called by the _deserialize_node wrapper on load)
    # ------------------------------------------------------------------
    def rebuild_module(self, node):
        if not isinstance(node.mesh_params, dict):
            node.mesh_params = {}
        p = node.mesh_params
        key = p.get("module_key", "wall")
        biome = p.get("biome", "military")
        cell = float(p.get("cell_size", _FP_CELL))
        fh = float(p.get("floor_height", _FP_FLOOR_H))
        if key == "glass_pane":
            node.mesh = self._mesh_for(key, "__glass__")
            node.material = self._glass_mat()
        else:
            rot = int(p.get("rotation_index", 0)) % 4
            node.mesh = self._mesh_for(
                key, biome, rot if key == "stairwell" else 0)
            node.material = self._material_for(biome, key)
            if key == "stairwell":
                # Yaw is baked into the mesh variant (see _mesh_for):
                # keep the node itself yaw-0 so the exact-triangle
                # MeshCollider matches the rendered stair.
                node.rotation_euler = glm.vec3(
                    float(node.rotation_euler.x), 0.0,
                    float(node.rotation_euler.z))
        node.mesh_type = "procedural_module"
        plan = p.get("collider")
        # v3 upgrade rule: stairwells ALWAYS collide with their exact
        # triangles (MeshCollider) and window frames / glass panes are
        # ALWAYS static thin boxes - a stale v2 save may carry a solid
        # stair box or a dynamic mass-5 window body in mesh_params, so
        # these three keys recompute their default plan on every load.
        if key in ("stairwell", "window_frame", "glass_pane"):
            plan = None
        if not plan:
            plan = _fp_default_collider(key, cell, fh)
            node.mesh_params["collider"] = plan
        self._apply_plan(node, plan)
        # Window frames always carry their MASK-glass child (old saves and
        # hand-built nodes might lack it); the child's own deserialize hook
        # rebuilds it when it round-trips through the serializer.
        if key == "window_frame":
            has_glass = any(
                (c.mesh_params or {}).get("module_key") == "glass_pane"
                for c in node.children)
            if not has_glass:
                gp = p.get("grid_pos") or [0, 0, 0]
                glass = self._spawn_module(
                    "glass_pane", biome, int(gp[0]), int(gp[1]),
                    int(gp[2]), 0, int(p.get("seed", 0)),
                    parent=node,
                    plan=_fp_default_collider("glass_pane", cell, fh),
                    register=False)
                glass.name = f"__fp_glass_{gp[0]}_{gp[1]}_{gp[2]}"
        # Re-register occupancy from the node's actual world transform so
        # painting/erasing stays consistent after a load.
        if key != "glass_pane":
            try:
                wp = node.world_position()
                Gx, Gy, Gz = self._grid_of(wp.x, wp.y, wp.z)
                self._occ_release(node)
                self._occ_claim(node, key, Gx, Gy, Gz)
            except Exception:
                pass
        return node

    # ------------------------------------------------------------------
    #  FPS-Creator grid painting (snap math, ghost, paint/erase)
    # ------------------------------------------------------------------
    def paint_active(self):
        """Explicit panel_open x mode state machine: building input is
        intercepted ONLY while the panel is OPEN and the mode is PAINT.
        There is no state where the paint tool eats clicks/keys while the
        panel appears closed."""
        return self.panel_open and self.mode == "paint"

    def set_mode(self, mode):
        if mode in self.MODES:
            self.mode = mode
            if mode != "paint":
                self._ghost = None
                self._raycast_hit_node = None

    def choose_module(self, key):
        """Palette click: select the module and enter PAINT mode."""
        if key in _FP_BUILDERS and key != "glass_pane":
            self.paint_key = key
            self.set_mode("paint")

    def adj_paint_floor(self, delta):
        self.paint_floor = max(0, min(16, self.paint_floor + int(delta)))

    @staticmethod
    def _ray_plane_y(ro, rd, y):
        """t of ray/plane intersection (y = const), or None.  Exact math:
        t = (y - ro.y) / rd.y, t > 0."""
        dy = float(rd.y)
        if abs(dy) < 1e-9:
            return None
        t = (float(y) - float(ro.y)) / dy
        return t if t > 0.0 else None

    def _paint_raycast(self, engine, ro, rd):
        """Resolve the cursor ray to a target grid cell.

        The mouse ray is intersected with the current-floor grid plane
        (y = paint_floor * floor_height).  If the ray hits an existing
        procedural module FIRST (CPU OBB raycast, the _pick_scene approach
        - the physics world is not populated at edit time, so a physics
        raycast would see nothing), the new module stacks on that module's
        top face (its occupancy cell + 1 floor) instead.
        Returns (gx, gy, gz, occupied) or None.  As a side effect the
        directly-hit module (if any) is remembered in
        self._raycast_hit_node so paint_click can resolve ERASE victims
        to the module actually hit instead of the stacked cell above."""
        cell, fh = _FP_CELL, _FP_FLOOR_H
        t_plane = self._ray_plane_y(ro, rd, self.paint_floor * fh)
        best_t = None
        best_node = None
        obb = getattr(HeavenEngine, "_raycast_obb", None)
        if callable(obb):
            for slot in self.occupancy.values():
                for lst in slot.values():
                    for node in lst:
                        mesh = getattr(node, "mesh", None)
                        if mesh is None:
                            continue
                        bb = getattr(mesh, "bounding_box", None)
                        if not bb:
                            continue
                        try:
                            t = obb(ro, rd, bb[0], bb[1],
                                    node.world_matrix())
                        except Exception:
                            t = None
                        if t is not None and (best_t is None
                                              or t < best_t):
                            best_t, best_node = t, node
        self._raycast_hit_node = None
        if best_node is not None and (t_plane is None
                                      or best_t <= t_plane):
            self._raycast_hit_node = best_node
            px = float(ro.x) + float(rd.x) * best_t
            py = float(ro.y) + float(rd.y) * best_t
            pz = float(ro.z) + float(rd.z) * best_t
            # Stack on the hit surface: the grid level of the surface
            # itself (a slab top at gy*fh lands in the slab's own cell, a
            # full-height wall top at (gy+1)*fh stacks one floor up).
            gy = max(0, int(round(py / fh)))
            gx = int(math.floor(px / cell + 1e-6))
            gz = int(math.floor(pz / cell + 1e-6))
        elif t_plane is not None:
            px = float(ro.x) + float(rd.x) * t_plane
            pz = float(ro.z) + float(rd.z) * t_plane
            gx = int(math.floor(px / cell + 1e-6))
            gz = int(math.floor(pz / cell + 1e-6))
            gy = self.paint_floor
        else:
            return None
        slot = self.occupancy.get((gx, gy, gz)) or {}
        occupied = bool(slot.get(self.paint_key))
        return gx, gy, gz, occupied

    def _erase_victim(self, gx, gy, gz):
        """Resolve which module an erase click removes.  When the cursor
        ray hit a module directly (even on its TOP face - _paint_raycast
        maps top faces to the cell ABOVE, which is right for
        paint-stacking but wrong for erase), the victim is the module
        actually hit.  Pure grid-plane hits erase from the mapped cell."""
        hit = self._raycast_hit_node
        if hit is not None:
            if "_fp_occ" in getattr(hit, "user_data", {}):
                return hit
            parent = getattr(hit, "parent", None)
            if (parent is not None
                    and "_fp_occ" in getattr(parent, "user_data", {})):
                return parent           # e.g. a window's glass child
            gy = max(0, gy - 1)  # unregistered hit: one level below
        slot = self.occupancy.get((gx, gy, gz)) or {}
        keys = [self.paint_key] + [k for k in slot if k != self.paint_key]
        for k in keys:
            lst = slot.get(k)
            if lst:
                return lst[-1]
        return None

    def paint_click(self, engine, ro, rd, erase=False):
        """LMB paints (creates the module, registers occupancy); Ctrl+LMB
        or RMB erases (engine._delete_node + occupancy free)."""
        tgt = self._paint_raycast(engine, ro, rd)
        if tgt is None:
            return
        gx, gy, gz, occupied = tgt
        if erase:
            victim = self._erase_victim(gx, gy, gz)
            if victim is not None:
                deleter = getattr(engine, "_delete_node", None)
                if callable(deleter):
                    deleter(victim)      # wrapper frees the occupancy
                else:
                    self._free_node(victim)
                    victim.remove()
            return
        if occupied:
            return                        # 3D grid prevents double placing
        biome = BiomeManager.ORDER[self.biome_index]
        seed = _fp_fnv1a("paint", biome, self.paint_key, gx, gy, gz,
                         self.paint_rot)
        node = self._spawn_module(
            self.paint_key, biome, gx, gy, gz, self.paint_rot, seed,
            parent=engine.scene.root,
            plan=_fp_default_collider(self.paint_key, _FP_CELL, _FP_FLOOR_H))
        # Window glass child (MASK material slot, static thin box -
        # z-adjacent to the frame's box, no RigidBody).
        if self.paint_key == "window_frame":
            glass = self._spawn_module(
                "glass_pane", biome, gx, gy, gz, 0, seed,
                parent=node,
                plan=_fp_default_collider("glass_pane", _FP_CELL,
                                          _FP_FLOOR_H),
                register=False)
            glass.name = f"__fp_glass_{gx}_{gy}_{gz}"
        if getattr(engine, "_playing", False):
            phys = getattr(engine, "physics", None)
            if phys is not None and getattr(node, "collider", None) is not None:
                try:
                    phys.add_node(node)
                except Exception:
                    pass

    def update_ghost(self, engine):
        """Per-frame (EditorGrid.render wrap): [ and ] rotate the ghost
        in 90-degree yaw steps (R also rotates it - intercepted by the
        _on_key wrapper while paint mode is active so the gizmo keeps
        its current mode); recompute the snapped target cell."""
        if not self.paint_active():
            self._ghost = None
            return
        inp = getattr(engine, "input", None)
        if inp is None:
            return
        try:
            if inp.was_key_pressed("["):
                self.paint_rot = (self.paint_rot - 1) % 4
            if inp.was_key_pressed("]"):
                self.paint_rot = (self.paint_rot + 1) % 4
            ro, rd = engine._mouse_ray(inp.mouse_pos)
            self._ghost = self._paint_raycast(engine, ro, rd)
        except Exception:
            self._ghost = None

    def ghost_lines(self):
        """Snapped footprint line-box + cell highlight; green = free,
        red = occupied."""
        if self._ghost is None:
            return None
        gx, gy, gz, occupied = self._ghost
        cell, fh = _FP_CELL, _FP_FLOOR_H
        x0, x1 = gx * cell, (gx + 1) * cell
        y0, y1 = gy * fh, gy * fh + fh
        z0, z1 = gz * cell, (gz + 1) * cell
        col = (1.0, 0.25, 0.20) if occupied else (0.20, 1.0, 0.35)
        o = []

        def seg(a, b):
            o.extend([a[0], a[1], a[2], col[0], col[1], col[2],
                      b[0], b[1], b[2], col[0], col[1], col[2]])

        for a, b in (((x0, y0, z0), (x1, y0, z0)),
                     ((x1, y0, z0), (x1, y0, z1)),
                     ((x1, y0, z1), (x0, y0, z1)),
                     ((x0, y0, z1), (x0, y0, z0)),
                     ((x0, y1, z0), (x1, y1, z0)),
                     ((x1, y1, z0), (x1, y1, z1)),
                     ((x1, y1, z1), (x0, y1, z1)),
                     ((x0, y1, z1), (x0, y1, z0)),
                     ((x0, y0, z0), (x0, y1, z0)),
                     ((x1, y0, z0), (x1, y1, z0)),
                     ((x1, y0, z1), (x1, y1, z1)),
                     ((x0, y0, z1), (x0, y1, z1))):
            seg(a, b)
        # Floor-cell highlight (raised cross, avoids z-fighting).
        yh = y0 + 0.02
        seg((x0, yh, z0), (x1, yh, z1))
        seg((x1, yh, z0), (x0, yh, z1))
        return o

    def draw_ghost(self, engine, camera):
        if not self.paint_active():
            return
        lines = self.ghost_lines()
        if lines:
            self._ghost_renderer.draw(engine, camera, lines)

    # ------------------------------------------------------------------
    #  D. Gizmo manual control: occupancy/mesh_params sync after drags
    # ------------------------------------------------------------------
    def sync_module_transform(self, node):
        """After a manual Gizmo drag: free the old cells, claim the cells
        implied by the FINAL world transform, and rewrite mesh_params
        (grid_pos / rotation_index) so serialization stays exact."""
        p = node.mesh_params
        if not isinstance(p, dict):
            return
        key = p.get("module_key")
        if not key or key == "glass_pane":
            return
        self._occ_release(node)
        try:
            wp = node.world_position()
        except Exception:
            wp = node.position
        gx, gy, gz = self._grid_of(float(wp.x), float(wp.y), float(wp.z))
        try:
            yaw = float(node.rotation_euler.y) % 360.0
        except Exception:
            yaw = 0.0
        rot = int(round(yaw / 90.0)) % 4
        if key == "stairwell":
            # Stair yaw lives in the mesh VARIANT (see _mesh_for): a
            # manual gizmo rotation is re-baked into the matching
            # variant and the node is normalised back to yaw-0, keeping
            # the exact-triangle MeshCollider identical to the visual.
            node.rotation_euler = glm.vec3(
                float(node.rotation_euler.x), 0.0,
                float(node.rotation_euler.z))
            node.mesh = self._mesh_for("stairwell",
                                       p.get("biome", "military"), rot)
        p["grid_pos"] = [gx, gy, gz]
        p["rotation_index"] = rot
        p["origin"] = [0.0, 0.0, 0.0]
        node.mesh_params = p
        self._occ_claim(node, key, gx, gy, gz)

    def sync_after_drag(self, target):
        """Gizmo.end_drag hook: sync the dragged module, or every module
        under a dragged sector root."""
        if target is None:
            return
        if getattr(target, "mesh_type", None) == "procedural_module":
            self.sync_module_transform(target)
            return
        for child in target.traverse():
            if getattr(child, "mesh_type", None) == "procedural_module":
                self.sync_module_transform(child)

    def snap_selected_to_grid(self):
        """SNAP TO GRID button: round the selected module (or every module
        under the selected sector root) back onto the 2.0 m grid / 90-degree
        yaw, then re-sync occupancy + mesh_params."""
        engine = self.engine
        node = getattr(engine, "selected", None)
        if node is None:
            return
        targets = []
        if getattr(node, "mesh_type", None) == "procedural_module":
            targets = [node]
        else:
            targets = [c for c in node.traverse()
                       if getattr(c, "mesh_type", None)
                       == "procedural_module"
                       and (c.mesh_params or {}).get("module_key")
                       != "glass_pane"]
        for mod in targets:
            try:
                wp = mod.world_position()
            except Exception:
                wp = mod.position
            gx, gy, gz = self._grid_of(float(wp.x), float(wp.y),
                                       float(wp.z))
            wx, wy, wz = self._cell_world(gx, gy, gz)
            try:
                parent = mod.parent
                if parent is not None:
                    inv = glm.inverse(parent.world_matrix())
                    lp = inv * glm.vec4(wx, wy, wz, 1.0)
                    mod.position = glm.vec3(lp.x, lp.y, lp.z)
                else:
                    mod.position = glm.vec3(wx, wy, wz)
            except Exception:
                mod.position = glm.vec3(wx, wy, wz)
            try:
                re = mod.rotation_euler
                rot = int(round(float(re.y) / 90.0)) % 4
                mod.rotation_euler = glm.vec3(float(re.x), rot * 90.0,
                                              float(re.z))
            except Exception:
                pass
            self.sync_module_transform(mod)

    # ------------------------------------------------------------------
    #  E. BUILDING EDITOR panel v3 (editor mode only - always ui-guarded)
    # ------------------------------------------------------------------
    def toggle_panel(self):
        """BUILDING EDITOR button: closing the panel (via the button or
        any other path) FULLY exits building mode; opening it never
        resurrects paint mode silently."""
        if self.panel_open:
            self.close_panel()
        else:
            self.open_panel()

    def open_panel(self):
        """Open the panel.  The previous palette selection (paint_key)
        is kept, but the panel always comes back in MANUAL (non-paint)
        mode if it was painting - paint mode is only ever entered by an
        explicit palette/PAINT click while the panel is open."""
        self.panel_open = True
        if self.mode == "paint":
            self.set_mode("manual")

    def close_panel(self):
        """Close the panel and fully exit building mode: paint mode off,
        ghost cleared (it stops being drawn because paint_active() is
        False), occupancy editing stopped - _fp_on_mouse_down falls
        through to the engine's original handler for 100% of clicks."""
        self.panel_open = False
        if self.mode == "paint":
            self.set_mode("manual")
        self._ghost = None
        self._raycast_hit_node = None

    def cycle_biome(self):
        self.biome_index = (self.biome_index + 1) % len(BiomeManager.ORDER)
        # Keep the palette selection valid for the new biome.
        mods = self._palette_modules()
        if self.paint_key not in mods:
            self.paint_key = "wall" if "wall" in mods else mods[0]

    def cycle_floors(self):
        self.floors = self.floors % 8 + 1

    def cycle_footprint(self):
        cur = tuple(self.footprint)
        try:
            i = self._foot_sizes.index(cur)
        except ValueError:
            i = 0
        self.footprint = list(self._foot_sizes[(i + 1)
                                               % len(self._foot_sizes)])

    def _palette_modules(self):
        biome = BiomeManager.ORDER[self.biome_index]
        cfg = BiomeManager.BIOMES[biome]
        return [k for k in cfg["modules"] if k in _FP_BUILDERS
                and k != "glass_pane"]

    def panel_generate(self):
        engine = self.engine
        biome = BiomeManager.ORDER[self.biome_index]
        spacing = self.footprint[0] * _FP_CELL + 4.0
        origin = (self._gen_count * spacing, 0.0, 0.0)
        self._gen_count += 1
        adder = getattr(engine, "add_procedural_sector", None)
        if callable(adder):
            adder(origin, biome, self.floors, tuple(self.footprint))

    def draw_panel(self, ui):
        """Immediate-mode panel, drawn every frame while open.  Only ever
        called from the _build_ui wrapper when engine.ui exists."""
        T = THEME
        biome = BiomeManager.ORDER[self.biome_index]
        ui.panel(UIRect(0.560, 0.468, 0.250, 0.454), T["panel_dark"])
        ui.panel(UIRect(0.560, 0.468, 0.250, 0.002), T["border"])
        ui.text(UIRect(0.570, 0.476, 0.230, 0.028),
                "Building Editor v3", T["text_bright"])
        # --- mode buttons ------------------------------------------------
        for i, (label, m) in enumerate((("GENERATE", "generate"),
                                        ("PAINT", "paint"),
                                        ("MANUAL", "manual"))):
            col = T["accent"] if self.mode == m else T["button"]
            ui.button(UIRect(0.570 + i * 0.078, 0.508, 0.074, 0.032),
                      label, lambda mm=m: self.set_mode(mm), col)
        # --- biome selector ----------------------------------------------
        ui.button(UIRect(0.570, 0.548, 0.230, 0.032),
                  f"Biome: {biome}", self.cycle_biome, T["button"])
        # --- paint floor level -------------------------------------------
        ui.text(UIRect(0.570, 0.590, 0.130, 0.028),
                f"Paint floor: {self.paint_floor}", T["text"])
        ui.button(UIRect(0.706, 0.588, 0.030, 0.032),
                  "-", lambda: self.adj_paint_floor(-1), T["button"])
        ui.button(UIRect(0.742, 0.588, 0.030, 0.032),
                  "+", lambda: self.adj_paint_floor(1), T["button"])
        # --- sector shape -------------------------------------------------
        ui.button(UIRect(0.570, 0.626, 0.113, 0.032),
                  f"Floors: {self.floors}", self.cycle_floors, T["button"])
        ui.button(UIRect(0.687, 0.626, 0.113, 0.032),
                  f"Foot: {self.footprint[0]}x{self.footprint[1]}",
                  self.cycle_footprint, T["button"])
        # --- module palette (from the current biome's modules) -----------
        ui.text(UIRect(0.570, 0.664, 0.230, 0.024),
                f"Palette ({self.paint_key}):", T["text_dim"])
        mods = self._palette_modules()
        for i, key in enumerate(mods):
            row, coln = divmod(i, 2)
            col = (T["accent_dim"] if (key == self.paint_key
                                       and self.mode == "paint")
                   else T["button"])
            ui.button(UIRect(0.570 + coln * 0.117, 0.692 + row * 0.030,
                             0.113, 0.026),
                      key, lambda k=key: self.choose_module(k), col)
        yb = 0.692 + ((len(mods) + 1) // 2) * 0.030 + 0.006
        # --- actions ------------------------------------------------------
        ui.button(UIRect(0.570, yb, 0.113, 0.032),
                  "GEN SECTOR", self.panel_generate, T["accent_dim"])
        ui.button(UIRect(0.687, yb, 0.113, 0.032),
                  "SNAP GRID", self.snap_selected_to_grid, T["accent_dim"])
        # --- key hints ----------------------------------------------------
        ui.text(UIRect(0.570, yb + 0.038, 0.230, 0.024),
                "R rotate | [ ] yaw | Ctrl+LMB/RMB erase",
                T["text_dim"])


# ---------------------------------------------------------------------------
#  D. Engine hook (installed by the integration wrappers below)
# ---------------------------------------------------------------------------

def _fp_add_procedural_sector(self, origin, biome, floors=1, footprint=(8, 8)):
    """HeavenEngine.add_procedural_sector - create + select a new sector."""
    gen = getattr(self, "procedural_generator", None)
    if gen is None:
        gen = ProceduralSectorGenerator(self)
        self.procedural_generator = gen
    root = gen.generate_sector(origin, biome, floors, footprint)
    try:
        self.select_node(root)
    except Exception as ex:
        Log.warn(f"[FinalPatch] select_node failed: {ex}")
    return root

# ---------------------------------------------------------------------------
#  Integration wrappers - defensive: never raise at import, always report.
#  Double-install guard: wrapping the monkey-patched methods a second time
#  would nest the wrappers around themselves -> infinite recursion, so the
#  wrapping below runs exactly once per module namespace (and is skipped if
#  a v1 install flag is somehow already present).
# ---------------------------------------------------------------------------

if globals().get("_FP2_INSTALLED"):
    print("[FinalPatch] procedural_sector_v3 already installed in this "
          "namespace - skipping monkey-patch wrapping (double-install "
          "guard).")
else:
    _FP2_INSTALLED = True

    # -- 1. serializer hook: procedural_module round-trip ------------------
    try:
        _FP_PREV_DESERIALIZE = ProjectSerializer._deserialize_node.__func__

        def _fp_deserialize_node(cls, data, parent, engine, project_dir):
            _FP_PREV_DESERIALIZE(cls, data, parent, engine, project_dir)
            try:
                if (data.get("mesh_type") == "procedural_module"
                        and parent.children):
                    node = parent.children[-1]
                    gen = getattr(engine, "procedural_generator", None)
                    if gen is None:
                        gen = ProceduralSectorGenerator(engine)
                        engine.procedural_generator = gen
                    gen.rebuild_module(node)
            except Exception as ex:
                Log.error(f"[FinalPatch] procedural_module rebuild "
                          f"failed: {ex}")

        ProjectSerializer._deserialize_node = \
            classmethod(_fp_deserialize_node)
    except Exception as _fp_ex:
        print(f"[FinalPatch] serializer hook failed: {_fp_ex}")

    # -- 2. engine init hook: generator + add_procedural_sector ------------
    try:
        _FP_PREV_INIT = HeavenEngine.__init__

        def _fp_engine_init(self, *args, **kwargs):
            global _FP_ENGINE
            _FP_PREV_INIT(self, *args, **kwargs)
            try:
                self.procedural_generator = ProceduralSectorGenerator(self)
                _FP_ENGINE = self
            except Exception as ex:
                self.procedural_generator = None
                Log.error(f"[FinalPatch] generator init failed: {ex}")

        HeavenEngine.__init__ = _fp_engine_init
        HeavenEngine.add_procedural_sector = _fp_add_procedural_sector
    except Exception as _fp_ex:
        print(f"[FinalPatch] engine __init__ hook failed: {_fp_ex}")

    # -- 3. BUILDING EDITOR panel hook -------------------------------------
    try:
        _FP_PREV_BUILD_UI = HeavenEngine._build_ui

        def _fp_build_ui(self):
            # With the Vitrail editor (h3d_editor) the classic layout is not
            # drawn, only the tool panels; package panels wrapped around
            # _build_ui still draw as usual.
            vt_editor = getattr(self, "_vt", None) is not None
            if vt_editor:
                if getattr(self, "ui", None) is not None:
                    self.ui.begin_frame()
                    # the view gizmo's X / Y / Z letters ride the UI pass
                    vg = getattr(self, "view_gizmo", None)
                    if vg is not None and not getattr(self, "_playing", False):
                        try:
                            vg.emit_labels(self.ui, self.width, self.height, self.camera)
                        except Exception:
                            pass
            else:
                _FP_PREV_BUILD_UI(self)
            try:
                ui = getattr(self, "ui", None)
                if ui is None or getattr(self, "_playing", False):
                    return
                gen = getattr(self, "procedural_generator", None)
                if gen is None:
                    return
                if not vt_editor:                  # (the Vitrail toolbar has its own button)
                    ui.button(UIRect(0.935, 0.944, 0.060, 0.042),
                              "BUILDING EDITOR", gen.toggle_panel,
                              THEME["accent_dim"])
                if gen.panel_open and not vt_editor:       # the Vitrail editor has its own panel
                    gen.draw_panel(ui)
            except Exception as ex:
                Log.warn(f"[FinalPatch] building editor UI skipped: {ex}")

        HeavenEngine._build_ui = _fp_build_ui
    except Exception as _fp_ex:
        print(f"[FinalPatch] _build_ui hook failed: {_fp_ex}")

    # -- 4. paint-mode click intercept (ColliderShapeEditor gating idiom) --
    try:
        _FP_PREV_ON_MOUSE_DOWN = HeavenEngine._on_mouse_down

        def _fp_on_mouse_down(self, button, pos):
            try:
                gen = getattr(self, "procedural_generator", None)
                if (gen is not None and gen.paint_active()
                        and getattr(self, "mode", None) == "editor"
                        and not getattr(self, "_playing", False)
                        and button in (1, 3)):
                    # Mirror the engine's own Alt+LMB orbit carve-out
                    # (HeavenEngine._on_mouse_down: "if self.input.alt:
                    # pass  # orbit"): never intercept Alt+click, the
                    # camera orbit must keep working while painting.
                    if button == 1 and bool(getattr(
                            getattr(self, "input", None), "alt", False)):
                        return _FP_PREV_ON_MOUSE_DOWN(self, button, pos)
                    # Let UI clicks behave exactly as usual: re-do the
                    # engine's own panel / navball swallow checks.
                    ux = pos[0] / float(self.width)
                    uy = 1.0 - pos[1] / float(self.height)
                    swallowed = False
                    ui = getattr(self, "ui", None)
                    if ui is not None:
                        try:
                            swallowed = bool(ui.point_in_panels(ux, uy))
                        except Exception:
                            swallowed = False
                    vg = getattr(self, "view_gizmo", None)
                    if vg is not None and not swallowed:
                        try:
                            swallowed = bool(vg.point_in_region(
                                pos, self.width, self.height))
                        except Exception:
                            pass
                    if not swallowed:
                        ro, rd = self._mouse_ray(pos)
                        erase = (button == 3) or bool(
                            getattr(getattr(self, "input", None),
                                    "ctrl", False))
                        gen.paint_click(self, ro, rd, erase=erase)
                        inp = getattr(self, "input", None)
                        if inp is not None:
                            inp.click_just_pressed = True
                            inp.click_pos = pos
                            if button == 1:
                                inp.lmb = True
                            elif button == 3:
                                inp.rmb = True
                        return        # consumed: no gizmo drag, no repick
            except Exception as ex:
                Log.warn(f"[FinalPatch] paint click skipped: {ex}")
            return _FP_PREV_ON_MOUSE_DOWN(self, button, pos)

        HeavenEngine._on_mouse_down = _fp_on_mouse_down
    except Exception as _fp_ex:
        print(f"[FinalPatch] _on_mouse_down hook failed: {_fp_ex}")

    # -- 5. per-frame ghost preview (EditorGrid.render wrap) ---------------
    try:
        _FP_PREV_GRID_RENDER = EditorGrid.render

        def _fp_editor_grid_render(self, camera):
            _FP_PREV_GRID_RENDER(self, camera)
            try:
                engine = _FP_ENGINE
                if engine is None:
                    return
                gen = getattr(engine, "procedural_generator", None)
                if gen is None or not gen.paint_active():
                    return
                gen.update_ghost(engine)
                gen.draw_ghost(engine, camera)
            except Exception:
                pass

        EditorGrid.render = _fp_editor_grid_render
    except Exception as _fp_ex:
        print(f"[FinalPatch] EditorGrid hook failed: {_fp_ex}")

    # -- 6. Gizmo manual-control sync --------------------------------------
    try:
        _FP_PREV_GIZMO_END_DRAG = Gizmo.end_drag

        def _fp_gizmo_end_drag(self):
            _FP_PREV_GIZMO_END_DRAG(self)
            try:
                target = getattr(self, "target", None)
                if target is None:
                    return
                engine = _FP_ENGINE
                gen = (getattr(engine, "procedural_generator", None)
                       if engine is not None else None)
                if gen is not None:
                    gen.sync_after_drag(target)
            except Exception as ex:
                Log.warn(f"[FinalPatch] gizmo sync skipped: {ex}")

        Gizmo.end_drag = _fp_gizmo_end_drag
    except Exception as _fp_ex:
        print(f"[FinalPatch] Gizmo hook failed: {_fp_ex}")

    # -- 7. deletion frees occupancy ---------------------------------------
    try:
        _FP_PREV_DELETE_NODE = HeavenEngine._delete_node

        def _fp_delete_node(self, node):
            try:
                gen = getattr(self, "procedural_generator", None)
                if gen is not None and node is not None:
                    gen._free_node(node)
            except Exception:
                pass
            return _FP_PREV_DELETE_NODE(self, node)

        HeavenEngine._delete_node = _fp_delete_node
    except Exception as _fp_ex:
        print(f"[FinalPatch] _delete_node hook failed: {_fp_ex}")

    # -- 8. R rotates the paint ghost (stock: R -> gizmo SCALE) ------------
    try:
        _FP_PREV_ON_KEY = HeavenEngine._on_key

        def _fp_on_key(self, key):
            try:
                gen = getattr(self, "procedural_generator", None)
                if (gen is not None and gen.paint_active()
                        and getattr(self, "mode", None) == "editor"
                        and not getattr(self, "_playing", False)
                        and key == pygame.K_r
                        and not (pygame.key.get_mods()
                                 & pygame.KMOD_CTRL)):
                    # While PAINT mode is active, R rotates the ghost
                    # +90 deg and is CONSUMED - the gizmo keeps its
                    # current mode (stock R -> SCALE is only reachable
                    # when the panel is closed / paint mode is off).
                    gen.paint_rot = (gen.paint_rot + 1) % 4
                    return
            except Exception as ex:
                Log.warn(f"[FinalPatch] paint R-rotate skipped: {ex}")
            return _FP_PREV_ON_KEY(self, key)

        HeavenEngine._on_key = _fp_on_key
    except Exception as _fp_ex:
        print(f"[FinalPatch] _on_key hook failed: {_fp_ex}")

    print("[FinalPatch] procedural_sector_v3 loaded (5 biomes, AAA PBR "
          "textures, procedural meshes, grid painting, Gizmo manual "
          "control, BUILDING EDITOR panel).")

# [FINALPATCH:procedural_sector_v3 END]




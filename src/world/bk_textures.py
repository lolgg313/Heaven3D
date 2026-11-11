# @heaven3d-part world/bk_textures.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# BUILDING MAKER - material library (WW2-era architecture)
#
# Tileable PBR sets (albedo / normal / roughness-metal / AO) made with numpy
# at real-world scale: every material says how many metres one tile covers,
# so a brick is 21 cm long on every wall.  Noise is spectral (FFT), so it is
# fast and tiles exactly.  The PNGs are cached on disk (~/.heaven3d/cache),
# so each material is generated once per machine.
# #############################################################################

_BK_TEX_VERSION = 10                 # bump to regenerate every cached texture
_BK_TEX_QUALITY = [1.0]             # 1.0 = full size, 0.5 = half-size textures


def _bk_rng(seed):
    return np.random.RandomState(int(seed) & 0x7FFFFFFF)


def _bk_freq(n):
    f = np.fft.fftfreq(n) * n
    return np.meshgrid(f, f)           # fx (columns), fy (rows)


def _bk_norm01(a):
    lo, hi = float(a.min()), float(a.max())
    return ((a - lo) / max(hi - lo, 1e-9)).astype(np.float32)


def _bk_noise(n, seed, beta=2.0, lo=1.0, hi=None, sx=1.0, sy=1.0):
    """Tileable spectral noise in [0, 1].  beta: spectral slope (2 = cloudy,
    1 = gritty, 0 = white); lo / hi: band in cycles per tile; sx / sy > 1
    stretch the features along x / y (streaks, wood grain)."""
    fx, fy = _bk_freq(n)
    f = np.sqrt((fx * sx) ** 2 + (fy * sy) ** 2)
    f[0, 0] = 1.0
    amp = f ** (-beta / 2.0)
    amp[f < lo] = 0.0
    if hi is not None:
        amp[f > hi] = 0.0
    amp[0, 0] = 0.0
    ph = _bk_rng(seed).uniform(0.0, 2.0 * math.pi, (n, n))
    a = np.fft.ifft2(amp * np.exp(1j * ph)).real
    a = (a - a.mean()) / max(float(a.std()), 1e-9)
    return np.clip(0.5 + a / 5.0, 0.0, 1.0).astype(np.float32)


def _bk_blur(a, sigma, sx=1.0, sy=1.0):
    """Periodic Gaussian blur (sigma in pixels; sx / sy scale it per axis)."""
    n = a.shape[0]
    fx, fy = _bk_freq(n)
    g = np.exp(-2.0 * (math.pi * sigma / n) ** 2 * ((fx * sx) ** 2 + (fy * sy) ** 2))
    if a.ndim == 3:
        return np.stack([np.fft.ifft2(np.fft.fft2(a[..., k]) * g).real for k in range(a.shape[2])],
                        -1).astype(np.float32)
    return np.fft.ifft2(np.fft.fft2(a) * g).real.astype(np.float32)


def _bk_ss(e0, e1, x):
    d = np.asarray(e1 - e0, np.float32)
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)
    t = np.clip((x - e0) / d, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _bk_mix(a, b, t):
    if a.ndim == 3 and np.ndim(t) == 2:
        t = t[..., None]
    return a * (1.0 - t) + b * t


def _bk_col(rgb):
    return np.asarray(rgb, np.float32)[None, None, :]


def _bk_grid(n):
    t = (np.arange(n, dtype=np.float32) + 0.5) / n
    return np.meshgrid(t, t)            # u (columns), v (rows)


def _bk_worley(n, cells, seed, jitter=0.9):
    """Periodic Worley: F1, F2 (in cell units) and the nearest cell's id."""
    u, v = _bk_grid(n)
    X, Y = u * cells, v * cells
    cx0, cy0 = np.floor(X).astype(np.int32), np.floor(Y).astype(np.int32)
    r = _bk_rng(seed)
    px = 0.5 + jitter * (r.rand(cells, cells) - 0.5)
    py = 0.5 + jitter * (r.rand(cells, cells) - 0.5)
    f1 = np.full((n, n), 1e9, np.float32)
    f2 = np.full((n, n), 1e9, np.float32)
    cid = np.zeros((n, n), np.int32)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            cx, cy = (cx0 + ox) % cells, (cy0 + oy) % cells
            dx = (cx0 + ox) + px[cy, cx] - X
            dy = (cy0 + oy) + py[cy, cx] - Y
            d = np.sqrt(dx * dx + dy * dy).astype(np.float32)
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            cid = np.where(closer, cy * cells + cx, cid)
            f1 = np.where(closer, d, f1)
    return f1, f2, cid


def _bk_cracks(n, seed, cells=7, width=0.012, warp=0.35):
    """Meandering hairline cracks in [0, 1] (1 = crack): the contour lines of
    a smooth noise field, shown only where a second field allows, so they
    wander, branch rarely and die out like cracks in old render.
    ``cells`` sets how busy the wall is, ``width`` the line width."""
    f = _bk_noise(n, seed, 2.2, 1.0, max(4.0, cells * 1.6))
    gx = np.roll(f, -1, 1) - np.roll(f, 1, 1)
    gy = np.roll(f, -1, 0) - np.roll(f, 1, 0)
    grad = np.sqrt(gx * gx + gy * gy) * 0.5 + 1e-6            # change per pixel
    px = max(0.7, width * 110.0 * n / 1024.0)                # half width in pixels
    line = 1.0 - _bk_ss(px * 0.5, px * 1.4, np.abs(f - 0.5) / grad)
    gate = _bk_ss(0.60, 0.70, _bk_noise(n, seed + 7, 2.4, 1, 5))
    fray = 0.55 + 0.45 * _bk_noise(n, seed + 9, 1.0, 20, None)
    return (line * gate * fray).astype(np.float32)


def _bk_streaks(n, seed, amount=1.0):
    """Vertical run-off streaks (rain, soot, rust) in [0, 1]."""
    a = _bk_noise(n, seed, 1.6, 2, None, sx=1.0, sy=0.06)
    b = _bk_noise(n, seed + 1, 2.0, 1, 6)
    return np.clip((a - 0.45) * 2.2 * (0.4 + b) * amount, 0.0, 1.0).astype(np.float32)


def _bk_bricks(n, cols, rows, seed, bond=0.5, wobble=0.25):
    """Brick / block / tile layout.  -> row, col (ints), fx, fy (0..1 inside
    the brick), per-brick random (n, n, 4)."""
    u, v = _bk_grid(n)
    if wobble:
        u = u + (_bk_noise(n, seed + 11, 2.0, 2, 20) - 0.5) * wobble / cols * 0.5
        v = v + (_bk_noise(n, seed + 12, 2.0, 2, 20) - 0.5) * wobble / rows * 0.5
    ys = v * rows
    r = np.floor(ys).astype(np.int32) % rows
    xs = u * cols + bond * (r % 2)
    c = np.floor(xs).astype(np.int32) % cols
    rnd = _bk_rng(seed).rand(rows, cols, 4).astype(np.float32)
    return r, c, (xs - np.floor(xs)).astype(np.float32), (ys - np.floor(ys)).astype(np.float32), rnd[r, c]


def _bk_edge(fx, fy, w, h):
    """Distance to the brick's edge in metres (w, h: brick size in metres)."""
    return np.minimum(np.minimum(fx, 1.0 - fx) * w, np.minimum(fy, 1.0 - fy) * h)


def _bk_palette(rnd, colors):
    """Pick a colour per brick from a list (rnd in [0, 1))."""
    cols = np.asarray(colors, np.float32)
    idx = np.minimum((rnd * len(cols)).astype(np.int32), len(cols) - 1)
    return cols[idx]


def _bk_finish(albedo, height, rough, metal=None, nstrength=3.0, ao_strength=1.0, alpha=None):
    """float maps -> dict of uint8 arrays (albedo, normal, mr, ao)."""
    n = height.shape[0]
    h = height.astype(np.float32)
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5
    k = float(nstrength) * n / 256.0
    nx, ny = -gx * k, gy * k                 # rows run top -> bottom, v runs up
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
    normal = np.stack([nx * inv, ny * inv, inv], -1)
    cav = _bk_blur(h, n / 180.0) - h
    cav2 = _bk_blur(h, n / 50.0) - h
    ao = np.clip(1.0 - ao_strength * (np.clip(cav, 0, None) * 5.0 + np.clip(cav2, 0, None) * 2.5), 0.25, 1.0)
    mr = np.zeros((n, n, 3), np.float32)
    mr[..., 1] = np.clip(rough, 0.04, 1.0)
    if metal is not None:
        mr[..., 2] = np.clip(metal, 0.0, 1.0)
    alb = np.clip(albedo, 0.0, 1.0)
    if alpha is not None:
        alb = np.concatenate([alb, np.clip(alpha, 0, 1)[..., None]], -1)
    u8 = lambda a: np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return {"albedo": u8(alb), "normal": u8(normal * 0.5 + 0.5), "mr": u8(mr), "ao": u8(ao)}


# ---------------------------------------------------------------------------
#  the materials
# ---------------------------------------------------------------------------
def _bk_layer_brick(n, seed, cols, rows, w, h, palette, mortar=(0.50, 0.47, 0.42), joint=0.011,
                    soot=0.5, variation=1.0):
    """-> (albedo, height, rough) of a weathered brick wall."""
    r, c, fx, fy, rnd = _bk_bricks(n, cols, rows, seed, 0.5, 0.35)
    fine = _bk_noise(n, seed + 1, 1.0, 16, None)
    grit = _bk_noise(n, seed + 2, 0.3, 60, None)
    cloud = _bk_noise(n, seed + 3, 2.4, 1, 6)
    d = _bk_edge(fx, fy, w, h) + (fine - 0.5) * 0.006
    jw = joint * (0.75 + 0.6 * rnd[..., 3])
    face = _bk_ss(jw * 0.6, jw * 1.5, d)                     # 0 in the joint, 1 on the face
    bev = _bk_ss(jw, jw + 0.012, d)
    base = _bk_palette(rnd[..., 0], palette)
    tone = 0.80 + 0.40 * rnd[..., 1:2] * variation
    alb = base * tone
    # fired-clay mottling, darker kiln marks, pale grit specks
    alb = alb * (0.86 + 0.28 * fine[..., None])
    kiln = _bk_ss(0.62, 0.80, _bk_noise(n, seed + 5, 1.8, 6, 60)) * (rnd[..., 2] > 0.55)
    alb = alb * (1.0 - 0.35 * kiln[..., None])
    specks = (grit > 0.80)[..., None] * 0.10
    alb = alb + specks * (1.0 - alb)
    # chipped / spalled faces: lighter raw clay, lower
    spall = _bk_ss(0.66, 0.74, _bk_noise(n, seed + 6, 1.6, 5, 50) * (0.75 + 0.5 * rnd[..., 2]))
    alb = _bk_mix(alb, alb * 1.25 + 0.04, spall * 0.6)
    mor = _bk_col(mortar) * (0.82 + 0.36 * _bk_noise(n, seed + 7, 1.2, 8, None)[..., None])
    mor = mor * (0.9 + 0.2 * cloud[..., None])
    alb = _bk_mix(mor, alb, face)
    # weathering: soot from above, damp darkening, pale lime bloom
    st = _bk_streaks(n, seed + 20)
    alb = alb * (1.0 - soot * 0.45 * st[..., None])
    alb = alb * (0.78 + 0.30 * cloud[..., None])
    bloom = _bk_ss(0.60, 0.80, _bk_noise(n, seed + 9, 2.2, 2, 12)) * 0.22
    alb = _bk_mix(alb, np.broadcast_to(_bk_col((0.72, 0.70, 0.66)), alb.shape), bloom * face)
    height = (0.42 + 0.10 * _bk_noise(n, seed + 8, 1.0, 20, None)) * (1.0 - bev) \
        + bev * (0.80 + 0.12 * rnd[..., 1] + 0.10 * (fine - 0.5) + 0.05 * (grit - 0.5) - 0.20 * spall)
    rough = _bk_mix(np.full((n, n), 0.96, np.float32), 0.80 + 0.12 * fine, face)
    return alb.astype(np.float32), height.astype(np.float32), rough.astype(np.float32)


_BK_BRICK_RED = ((0.43, 0.21, 0.16), (0.47, 0.24, 0.18), (0.39, 0.19, 0.15), (0.50, 0.28, 0.21),
                 (0.35, 0.18, 0.15), (0.45, 0.23, 0.19), (0.30, 0.17, 0.15), (0.51, 0.31, 0.23),
                 (0.38, 0.23, 0.19), (0.33, 0.21, 0.18))
_BK_BRICK_DARK = ((0.27, 0.15, 0.12), (0.31, 0.17, 0.13), (0.23, 0.14, 0.12), (0.34, 0.20, 0.15),
                  (0.20, 0.13, 0.11), (0.29, 0.19, 0.16))
_BK_BRICK_YELLOW = ((0.60, 0.50, 0.33), (0.55, 0.45, 0.30), (0.64, 0.55, 0.38), (0.50, 0.42, 0.29),
                    (0.58, 0.46, 0.30), (0.46, 0.38, 0.27))


def _bk_m_brick(n, seed, palette=_BK_BRICK_RED, soot=0.5, mortar=(0.50, 0.47, 0.42)):
    # 20 x 40 bricks on 4.5 m x 3.0 m: 225 x 75 mm with the joint
    alb, h, ro = _bk_layer_brick(n, seed, 20, 40, 0.225, 0.075, palette, mortar, soot=soot)
    return _bk_finish(alb, h, ro, nstrength=3.4), (4.5, 3.0)


def _bk_layer_plaster(n, seed, color, stain=0.5, crack=0.6, coarse=0.5):
    cloud = _bk_noise(n, seed, 2.6, 1, 5)
    blot = _bk_noise(n, seed + 1, 2.0, 3, 30)
    fine = _bk_noise(n, seed + 2, 0.8, 30, None)
    trowel = _bk_noise(n, seed + 3, 1.4, 6, 80, sx=0.35, sy=1.0)
    alb = _bk_col(color) * (0.80 + 0.26 * cloud[..., None]) * (0.92 + 0.14 * blot[..., None])
    alb = alb * (0.95 + 0.08 * fine[..., None])
    # warm / damp patches
    damp = _bk_ss(0.55, 0.8, _bk_noise(n, seed + 4, 2.4, 1, 7))
    alb = _bk_mix(alb, alb * _bk_col((0.78, 0.74, 0.66)), damp * 0.6 * stain)
    st = _bk_streaks(n, seed + 5)
    alb = alb * (1.0 - 0.38 * stain * st[..., None])
    cr = _bk_cracks(n, seed + 6, 5, 0.014) * crack
    hair = _bk_cracks(n, seed + 16, 11, 0.008) * crack * 0.5
    cr = np.maximum(cr, hair)
    alb = alb * (1.0 - 0.42 * cr[..., None])
    height = 0.70 + coarse * (0.10 * (trowel - 0.5) + 0.06 * (fine - 0.5) + 0.08 * (blot - 0.5)) - 0.30 * cr
    rough = 0.86 + 0.10 * (fine - 0.5) - 0.06 * damp
    return alb.astype(np.float32), height.astype(np.float32), rough.astype(np.float32)


def _bk_m_plaster(n, seed, color=(0.74, 0.66, 0.50), exposed=0.28, stain=0.6, brick=_BK_BRICK_RED):
    """Old render over brick: stained, cracked, fallen away in patches."""
    alb, h, ro = _bk_layer_plaster(n, seed, color, stain)
    if exposed > 0:
        balb, bh, bro = _bk_layer_brick(n, seed + 50, 20, 40, 0.225, 0.075, brick, soot=0.3)
        m = _bk_noise(n, seed + 60, 2.6, 1.0, 6) + 0.10 * (_bk_noise(n, seed + 61, 1.2, 10, 90) - 0.5)
        thr = float(np.quantile(m, 1.0 - exposed))
        hole = _bk_ss(thr - 0.004, thr + 0.004, m)                 # 1 = plaster gone
        rim = _bk_ss(thr - 0.030, thr, m) * (1.0 - hole)           # crumbling edge
        alb = alb * (1.0 - 0.18 * rim[..., None])
        alb = _bk_mix(alb, balb * 0.92, hole)
        h = _bk_mix(h, bh * 0.55, hole) + 0.03 * rim
        ro = _bk_mix(ro, bro, hole)
    return _bk_finish(alb, h, ro, nstrength=3.0), (4.5, 3.0)


def _bk_m_wallpaper(n, seed, a=(0.52, 0.55, 0.42), b=(0.66, 0.64, 0.50)):
    """Faded striped wallpaper, water-stained, peeling to bare plaster."""
    u, v = _bk_grid(n)
    stripes = 30
    s = np.abs(((u * stripes) % 1.0) - 0.5) * 2.0
    band = _bk_ss(0.42, 0.50, s)
    thin = _bk_ss(0.90, 0.94, s) + (1.0 - _bk_ss(0.04, 0.08, s))
    motif = _bk_ss(0.25, 0.05, np.hypot(((u * stripes) % 1.0) - 0.5, ((v * stripes * 0.75) % 1.0) - 0.5))
    alb = _bk_mix(np.broadcast_to(_bk_col(a), (n, n, 3)), np.broadcast_to(_bk_col(b), (n, n, 3)), band)
    alb = alb * (1.0 - 0.18 * thin[..., None]) * (1.0 - 0.12 * (motif * band)[..., None])
    cloud = _bk_noise(n, seed, 2.5, 1, 6)
    fine = _bk_noise(n, seed + 1, 0.6, 40, None)
    alb = alb * (0.80 + 0.28 * cloud[..., None]) * (0.96 + 0.08 * fine[..., None])
    st = _bk_streaks(n, seed + 2)
    alb = _bk_mix(alb, alb * _bk_col((0.70, 0.60, 0.42)), (st * 0.8)[..., None])
    palb, ph, pro = _bk_layer_plaster(n, seed + 30, (0.72, 0.68, 0.60), 0.4, 0.5)
    m = _bk_noise(n, seed + 40, 2.2, 2, 14)
    thr = float(np.quantile(m, 0.86))
    hole = _bk_ss(thr - 0.004, thr + 0.004, m)
    curl = _bk_ss(thr - 0.03, thr, m) * (1.0 - hole)
    alb = _bk_mix(alb * (1.0 + 0.10 * curl[..., None]), palb, hole)
    seam = (1.0 - _bk_ss(0.0, 0.004, np.abs(((u * 5) % 1.0) - 0.5) - 0.0)) * 0.0
    h = _bk_mix(0.72 + 0.02 * (fine - 0.5) + 0.05 * curl + seam, ph * 0.9, hole)
    ro = _bk_mix(0.78 + 0.08 * (fine - 0.5), pro, hole)
    return _bk_finish(alb, h, ro, nstrength=2.2), (3.0, 3.0)


def _bk_m_concrete(n, seed, color=(0.50, 0.49, 0.46)):
    """Board-formed bunker concrete: plank bands, tie holes, pits, run-off."""
    u, v = _bk_grid(n)
    boards = 20                                              # 15 cm boards on 3 m
    yb = v * boards
    bi = np.floor(yb).astype(np.int32) % boards
    fb = yb - np.floor(yb)
    r = _bk_rng(seed)
    tone = r.rand(boards).astype(np.float32)[bi]
    lift = r.rand(boards).astype(np.float32)[bi]
    grain = _bk_noise(n, seed + 1, 1.3, 3, None, sx=0.05, sy=1.0)
    cloud = _bk_noise(n, seed + 2, 2.6, 1, 5)
    fine = _bk_noise(n, seed + 3, 0.5, 40, None)
    seam = 1.0 - _bk_ss(0.0, 0.06, np.minimum(fb, 1.0 - fb))
    f1, f2, _ = _bk_worley(n, 90, seed + 4, 1.0)
    pits = (1.0 - _bk_ss(0.05, 0.16, f1)) * _bk_ss(0.5, 0.7, _bk_noise(n, seed + 5, 2.0, 2, 12))
    # form-tie holes on a 0.75 m x 0.6 m grid
    hu, hv = (u * 6.0) % 1.0 - 0.5, (v * 5.0) % 1.0 - 0.5
    tie = 1.0 - _bk_ss(0.012, 0.020, np.hypot(hu / 5.0 * 4.5, hv / 5.0 * 3.0) * 1.0)
    alb = _bk_col(color) * (0.78 + 0.30 * cloud[..., None]) * (0.90 + 0.14 * tone[..., None])
    alb = alb * (0.93 + 0.14 * grain[..., None]) * (0.96 + 0.08 * fine[..., None])
    st = _bk_streaks(n, seed + 6)
    alb = alb * (1.0 - 0.42 * st[..., None])
    rust = _bk_blur(tie, n / 40.0, 0.3, 1.0) * 6.0
    rust = np.clip(np.roll(rust, n // 30, 0), 0, 1) * 0.5
    alb = _bk_mix(alb, alb * _bk_col((0.80, 0.56, 0.36)), rust)
    moss = _bk_ss(0.68, 0.85, _bk_noise(n, seed + 7, 2.3, 2, 10)) * 0.35
    alb = _bk_mix(alb, alb * _bk_col((0.62, 0.72, 0.48)), moss)
    alb = alb * (1.0 - 0.5 * pits[..., None]) * (1.0 - 0.6 * tie[..., None]) * (1.0 - 0.25 * seam[..., None])
    cr = _bk_cracks(n, seed + 8, 4, 0.008) * 0.8
    alb = alb * (1.0 - 0.5 * cr[..., None])
    h = 0.62 + 0.05 * (lift - 0.5) + 0.07 * (grain - 0.5) + 0.05 * seam - 0.30 * pits - 0.45 * tie - 0.25 * cr \
        + 0.03 * (fine - 0.5)
    ro = 0.80 + 0.12 * (fine - 0.5) + 0.08 * pits
    return _bk_finish(alb, h, ro, nstrength=2.6), (4.5, 3.0)


def _bk_m_concrete_floor(n, seed):
    cloud = _bk_noise(n, seed, 2.6, 1, 6)
    fine = _bk_noise(n, seed + 1, 0.6, 30, None)
    mid = _bk_noise(n, seed + 2, 1.8, 4, 40)
    alb = _bk_col((0.44, 0.43, 0.41)) * (0.74 + 0.36 * cloud[..., None]) * (0.92 + 0.16 * mid[..., None])
    oil = _bk_ss(0.70, 0.86, _bk_noise(n, seed + 3, 2.4, 2, 9))
    alb = alb * (1.0 - 0.45 * oil[..., None])
    cr = np.maximum(_bk_cracks(n, seed + 4, 4, 0.010), _bk_cracks(n, seed + 5, 9, 0.006) * 0.6)
    u, v = _bk_grid(n)
    joint = 1.0 - _bk_ss(0.0, 0.006, np.minimum(np.abs(u - 0.5), np.abs(v - 0.5)))
    alb = alb * (1.0 - 0.6 * cr[..., None]) * (1.0 - 0.55 * joint[..., None])
    h = 0.6 + 0.04 * (mid - 0.5) + 0.03 * (fine - 0.5) - 0.3 * cr - 0.35 * joint
    ro = 0.74 + 0.14 * (fine - 0.5) - 0.30 * oil
    return _bk_finish(alb, h, ro, nstrength=1.8), (4.0, 4.0)


def _bk_layer_wood(n, seed, planks, color, along="u", gap=0.012, joints=3, wear=0.5):
    """Boards across the tile.  -> albedo, height, rough, plank mask."""
    u, v = _bk_grid(n)
    if along == "v":
        u, v = v, u
    yb = v * planks
    pi_ = np.floor(yb).astype(np.int32) % planks
    fp = yb - np.floor(yb)
    r = _bk_rng(seed)
    tone = r.rand(planks).astype(np.float32)[pi_]
    shift = r.rand(planks).astype(np.float32)[pi_]
    us = (u + shift) % 1.0
    # grain: stretched noise + ring lines bent around knots
    sx, sy = (0.03, 1.0) if along == "u" else (1.0, 0.03)
    g1 = _bk_noise(n, seed + 1, 1.2, 4, None, sx=sx, sy=sy)
    g2 = _bk_noise(n, seed + 2, 2.0, 2, 30, sx=sx * 4, sy=sy * 4 if along == "v" else sy)
    rings = 0.5 + 0.5 * np.sin((fp * 9.0 + g2 * 14.0 + tone * 20.0) * 2.0 * math.pi)
    grain = np.clip(0.5 + (0.55 * g1 + 0.45 * rings * (0.4 + 0.6 * g2) - 0.5) * 1.7, 0.0, 1.0)
    fine = _bk_noise(n, seed + 3, 0.6, 40, None)
    alb = _bk_col(color) * (0.66 + 0.62 * tone[..., None]) * (0.66 + 0.62 * grain[..., None])
    alb = alb * (0.96 + 0.08 * fine[..., None])
    zero = np.zeros((n, n), np.float32)
    gapm = (1.0 - _bk_ss(gap, gap * 2.5, np.minimum(fp, 1.0 - fp))) if gap > 0 else zero
    jn = max(1, joints)
    jf = (us * jn) % 1.0
    jm = (1.0 - _bk_ss(0.002 * jn, 0.005 * jn, np.minimum(jf, 1.0 - jf))) if joints > 0 else zero
    gaps = np.maximum(gapm, jm)
    # nails near the board ends
    nd = np.hypot((np.minimum(jf, 1.0 - jf) / jn - 0.012) * 4.0, (np.abs(fp - 0.5) - 0.28) / planks * 1.2)
    nail = (1.0 - _bk_ss(0.004, 0.007, nd)) if joints > 0 else zero
    dirt = _bk_noise(n, seed + 4, 2.4, 1, 8)
    alb = alb * (0.80 + 0.26 * dirt[..., None])
    worn = _bk_ss(0.55, 0.85, _bk_noise(n, seed + 5, 2.2, 2, 12)) * wear
    alb = _bk_mix(alb, alb * 1.25 + 0.03, worn * 0.5)
    alb = alb * (1.0 - 0.80 * gaps[..., None]) * (1.0 - 0.6 * nail[..., None])
    cup = (np.abs(fp - 0.5) * 2.0) ** 2
    h = 0.66 + 0.05 * (tone - 0.5) + 0.07 * (grain - 0.5) - 0.04 * cup - 0.50 * gaps - 0.15 * nail
    ro = 0.62 + 0.20 * (grain - 0.5) + 0.12 * (1.0 - worn) * 0.5 + 0.2 * gaps
    return alb.astype(np.float32), h.astype(np.float32), ro.astype(np.float32)


def _bk_m_wood_floor(n, seed):
    alb, h, ro = _bk_layer_wood(n, seed, 16, (0.40, 0.29, 0.18), "u", 0.035, 2, 0.7)
    return _bk_finish(alb, h, ro, nstrength=2.4), (2.0, 2.0)


def _bk_m_wood_trim(n, seed, color=(0.24, 0.16, 0.10)):
    alb, h, ro = _bk_layer_wood(n, seed, 6, color, "u", 0.02, 1, 0.3)
    return _bk_finish(alb, h, ro, nstrength=2.0), (1.5, 0.9)


def _bk_m_wood_beam(n, seed, color=(0.21, 0.14, 0.09)):
    """One piece of dark old timber (beams, frames, linings): grain, checks
    and adze marks, no board joints."""
    alb, h, ro = _bk_layer_wood(n, seed, 1, color, "u", 0.0, 0, 0.25)
    chk = _bk_ss(0.90, 0.95, _bk_noise(n, seed + 30, 1.0, 6, None, sx=0.04, sy=1.0))
    alb = alb * (1.0 - 0.5 * chk[..., None])
    return _bk_finish(alb, h - 0.2 * chk, ro, nstrength=2.2), (1.5, 0.75)


def _bk_m_painted_wood(n, seed, paint=(0.20, 0.30, 0.22)):
    """Boards with old paint flaking off along the grain and edges."""
    alb, h, ro = _bk_layer_wood(n, seed, 8, (0.36, 0.28, 0.20), "v", 0.03, 1, 0.2)
    chip = _bk_noise(n, seed + 20, 1.6, 4, None, sx=1.0, sy=0.25) + 0.25 * (_bk_noise(n, seed + 21, 2.2, 1, 8) - 0.5)
    thr = float(np.quantile(chip, 0.78))
    bare = _bk_ss(thr - 0.006, thr + 0.006, chip)
    cloud = _bk_noise(n, seed + 22, 2.4, 1, 6)
    pc = _bk_col(paint) * (0.78 + 0.34 * cloud[..., None])
    gaps = _bk_ss(0.25, 0.10, h)
    alb = _bk_mix(pc * (1.0 - 0.6 * gaps[..., None]), alb * 0.9, bare)
    h = h * 0.6 + 0.3 + 0.05 * (1.0 - bare)
    ro = _bk_mix(np.full((n, n), 0.55, np.float32), ro, bare)
    return _bk_finish(alb, h, ro, nstrength=2.2), (1.2, 1.2)


def _bk_m_roof_tiles(n, seed, palette=((0.45, 0.25, 0.17), (0.40, 0.21, 0.15), (0.49, 0.29, 0.20),
                                       (0.35, 0.20, 0.16), (0.43, 0.27, 0.21), (0.31, 0.19, 0.16))):
    """Overlapping plain clay tiles: 12 across x 16 courses on 2 m x 1.6 m."""
    cols, rows = 12, 16
    r, c, fx, fy, rnd = _bk_bricks(n, cols, rows, seed, 0.5, 0.12)
    fine = _bk_noise(n, seed + 1, 0.9, 20, None)
    cloud = _bk_noise(n, seed + 2, 2.5, 1, 6)
    base = _bk_palette(rnd[..., 0], palette) * (0.80 + 0.40 * rnd[..., 1:2])
    alb = base * (0.88 + 0.22 * fine[..., None])
    # each tile is thickest at its lower edge (rows run top -> bottom) and
    # is tucked under the course above
    lower = fy                                               # 0 top of tile, 1 lower edge
    side = np.minimum(fx, 1.0 - fx)
    sgap = 1.0 - _bk_ss(0.015, 0.05, side)
    lip = _bk_ss(0.86, 0.985, lower) * (1.0 - _bk_ss(0.985, 1.0, lower))
    shadow = 1.0 - _bk_ss(0.0, 0.16, lower)                  # under the course above
    camber = 1.0 - (np.abs(fx - 0.5) * 2.0) ** 2
    h = 0.30 + 0.48 * lower + 0.08 * camber + 0.05 * (rnd[..., 2] - 0.5) + 0.04 * (fine - 0.5) - 0.35 * sgap
    alb = alb * (1.0 - 0.45 * shadow[..., None]) * (1.0 - 0.6 * sgap[..., None])
    moss = _bk_ss(0.60, 0.78, _bk_noise(n, seed + 3, 2.2, 2, 14) + 0.25 * shadow - 0.1)
    alb = _bk_mix(alb, alb * 0.4 + _bk_col((0.16, 0.20, 0.10)) * 0.6, moss * 0.75)
    lich = _bk_ss(0.86, 0.90, _bk_noise(n, seed + 4, 1.4, 14, 90))
    alb = _bk_mix(alb, np.broadcast_to(_bk_col((0.55, 0.55, 0.46)), alb.shape), lich * 0.3)
    alb = alb * (0.80 + 0.26 * cloud[..., None])
    ro = 0.82 + 0.10 * (fine - 0.5) + 0.08 * moss
    return _bk_finish(alb, h, ro, nstrength=3.6, ao_strength=1.3), (2.0, 1.6)


def _bk_m_slate(n, seed):
    return _bk_m_roof_tiles(n, seed, ((0.25, 0.26, 0.28), (0.21, 0.22, 0.24), (0.29, 0.30, 0.31),
                                      (0.23, 0.24, 0.24), (0.27, 0.26, 0.27), (0.24, 0.25, 0.28)))


def _bk_m_stone(n, seed, color=(0.50, 0.47, 0.41), cols=5, rows=8):
    """Rock-faced ashlar: 60 x 30 cm blocks on 3.0 m x 2.4 m."""
    r, c, fx, fy, rnd = _bk_bricks(n, cols, rows, seed, 0.5, 0.5)
    d = _bk_edge(fx, fy, 3.0 / cols, 2.4 / rows) + (_bk_noise(n, seed + 1, 1.2, 10, None) - 0.5) * 0.02
    face = _bk_ss(0.008, 0.022, d)
    rock = _bk_noise(n, seed + 2, 1.9, 4, None)
    fine = _bk_noise(n, seed + 3, 0.7, 30, None)
    cloud = _bk_noise(n, seed + 4, 2.5, 1, 6)
    bulge = _bk_ss(0.0, 0.10, d)
    tint = _bk_palette(rnd[..., 0], ((1.0, 1.0, 1.0), (1.06, 1.0, 0.92), (0.92, 0.94, 0.98), (1.04, 0.98, 0.90),
                                     (0.86, 0.86, 0.86)))
    alb = _bk_col(color) * tint * (0.80 + 0.36 * rnd[..., 1:2]) * (0.76 + 0.48 * rock[..., None])
    alb = alb * (0.95 + 0.10 * fine[..., None])
    mor = _bk_col((0.42, 0.40, 0.36)) * (0.85 + 0.3 * fine[..., None])
    alb = _bk_mix(mor, alb, face)
    st = _bk_streaks(n, seed + 5)
    alb = alb * (1.0 - 0.35 * st[..., None]) * (0.80 + 0.26 * cloud[..., None])
    moss = _bk_ss(0.70, 0.85, _bk_noise(n, seed + 6, 2.2, 2, 12)) * (1.0 - face * 0.6)
    alb = _bk_mix(alb, alb * _bk_col((0.55, 0.68, 0.40)), moss * 0.6)
    h = 0.30 * (1 - face) + face * (0.45 + 0.30 * bulge + 0.30 * (rock - 0.5) + 0.08 * (rnd[..., 2] - 0.5))
    ro = 0.86 + 0.10 * (fine - 0.5)
    return _bk_finish(alb, h, ro, nstrength=3.6, ao_strength=1.2), (3.0, 2.4)


def _bk_m_cobble(n, seed):
    """Rounded setts with dirt between them, 2.4 m x 2.4 m."""
    cells = 18
    f1, f2, cid = _bk_worley(n, cells, seed, 0.75)
    r = _bk_rng(seed + 1)
    tone = r.rand(cells * cells).astype(np.float32)[cid]
    hue = r.rand(cells * cells).astype(np.float32)[cid]
    edge = (f2 - f1)
    stone = _bk_ss(0.07, 0.20, edge)
    dome = _bk_ss(0.0, 0.5, edge) ** 0.5
    fine = _bk_noise(n, seed + 2, 0.8, 30, None)
    cloud = _bk_noise(n, seed + 3, 2.5, 1, 6)
    base = _bk_palette(hue, ((0.36, 0.34, 0.31), (0.41, 0.38, 0.33), (0.31, 0.30, 0.28), (0.43, 0.38, 0.31),
                             (0.33, 0.30, 0.26), (0.38, 0.36, 0.34)))
    alb = base * (0.75 + 0.5 * tone[..., None]) * (0.9 + 0.2 * fine[..., None])
    alb = alb * (0.75 + 0.35 * dome[..., None])
    dirt = _bk_col((0.20, 0.17, 0.13)) * (0.8 + 0.4 * fine[..., None])
    alb = _bk_mix(dirt, alb, stone) * (0.82 + 0.24 * cloud[..., None])
    h = stone * (0.45 + 0.40 * dome + 0.10 * (tone - 0.5) + 0.05 * (fine - 0.5)) + (1 - stone) * 0.25
    ro = _bk_mix(np.full((n, n), 0.95, np.float32), 0.62 + 0.2 * fine, stone)
    return _bk_finish(alb, h, ro, nstrength=3.4, ao_strength=1.3), (2.4, 2.4)


def _bk_m_checker(n, seed, a=(0.70, 0.67, 0.60), b=(0.13, 0.12, 0.12)):
    """Worn black-and-white hall tiles, 20 cm, on 2 m x 2 m."""
    t = 10
    r, c, fx, fy, rnd = _bk_bricks(n, t, t, seed, 0.0, 0.05)
    d = _bk_edge(fx, fy, 0.2, 0.2)
    face = _bk_ss(0.002, 0.006, d)
    chk = ((r + c) % 2).astype(np.float32)[..., None]
    fine = _bk_noise(n, seed + 1, 0.8, 30, None)
    cloud = _bk_noise(n, seed + 2, 2.5, 1, 6)
    alb = (_bk_col(a) * (1 - chk) + _bk_col(b) * chk) * (0.88 + 0.22 * rnd[..., 1:2])
    cr = _bk_cracks(n, seed + 3, 8, 0.006) * (rnd[..., 2] > 0.6)
    scuff = _bk_ss(0.55, 0.8, _bk_noise(n, seed + 4, 1.8, 4, 50))
    alb = _bk_mix(alb, alb * 0.8 + 0.1, scuff[..., None] * 0.5)
    alb = alb * (1.0 - 0.6 * cr[..., None])
    grout = _bk_col((0.25, 0.23, 0.20))
    alb = _bk_mix(np.broadcast_to(grout, alb.shape), alb, face) * (0.78 + 0.30 * cloud[..., None])
    h = face * (0.70 + 0.04 * (rnd[..., 3] - 0.5)) + (1 - face) * 0.50 - 0.2 * cr + 0.02 * (fine - 0.5)
    ro = _bk_mix(np.full((n, n), 0.9, np.float32), 0.30 + 0.35 * scuff + 0.1 * fine, face)
    return _bk_finish(alb, h, ro, nstrength=1.6), (2.0, 2.0)


def _bk_m_corrugated(n, seed):
    """Rusting corrugated iron sheet, 2 m x 2 m."""
    u, v = _bk_grid(n)
    wave = 0.5 + 0.5 * np.cos(u * 26.0 * 2.0 * math.pi)
    fine = _bk_noise(n, seed, 0.8, 30, None)
    cloud = _bk_noise(n, seed + 1, 2.4, 1, 6)
    m = _bk_noise(n, seed + 2, 2.0, 2, 30) + 0.5 * _bk_streaks(n, seed + 3) + 0.2 * (1.0 - wave) - 0.1
    thr = float(np.quantile(m, 0.50))
    rust = _bk_ss(thr - 0.05, thr + 0.05, m)
    deep = _bk_ss(thr + 0.10, thr + 0.22, m)
    zinc = _bk_col((0.50, 0.52, 0.52)) * (0.82 + 0.30 * cloud[..., None]) * (0.92 + 0.16 * fine[..., None])
    rc = _bk_mix(np.broadcast_to(_bk_col((0.47, 0.25, 0.12)), (n, n, 3)),
                 np.broadcast_to(_bk_col((0.24, 0.12, 0.08)), (n, n, 3)), deep)
    rc = rc * (0.80 + 0.40 * fine[..., None])
    alb = _bk_mix(zinc, rc, rust)
    sheet = 1.0 - _bk_ss(0.0, 0.008, np.minimum(np.abs(v - 0.5), np.minimum(v, 1 - v)))
    alb = alb * (0.62 + 0.38 * wave[..., None]) * (1.0 - 0.5 * sheet[..., None])
    h = 0.25 + 0.55 * wave + 0.05 * rust * (fine - 0.5) - 0.2 * sheet
    ro = _bk_mix(0.38 + 0.2 * fine, np.full((n, n), 0.92, np.float32), rust)
    metal = (1.0 - rust) * 0.85
    return _bk_finish(alb, h, ro, metal, nstrength=3.0), (2.0, 2.0)


def _bk_m_steel(n, seed, paint=(0.20, 0.23, 0.20)):
    """Painted steel plate: rivets, scratches, rust creeping from the edges."""
    u, v = _bk_grid(n)
    fine = _bk_noise(n, seed, 0.7, 30, None)
    cloud = _bk_noise(n, seed + 1, 2.4, 1, 6)
    ru, rv = (u * 8.0) % 1.0 - 0.5, (v * 8.0) % 1.0 - 0.5
    rivet = (1.0 - _bk_ss(0.07, 0.11, np.hypot(ru, rv))) * ((np.floor(v * 8) % 4 == 0) | (np.floor(u * 8) % 4 == 0))
    m = _bk_noise(n, seed + 2, 1.9, 2, 40) + 0.35 * _bk_streaks(n, seed + 3) + 0.25 * rivet
    thr = float(np.quantile(m, 0.74))
    rust = _bk_ss(thr - 0.03, thr + 0.03, m)
    scr = _bk_ss(0.90, 0.94, _bk_noise(n, seed + 4, 0.8, 20, None, sx=0.08, sy=1.0))
    pc = _bk_col(paint) * (0.78 + 0.36 * cloud[..., None]) * (0.94 + 0.12 * fine[..., None])
    rc = _bk_col((0.36, 0.19, 0.10)) * (0.7 + 0.6 * fine[..., None])
    alb = _bk_mix(pc, rc, rust)
    alb = _bk_mix(alb, np.broadcast_to(_bk_col((0.55, 0.56, 0.56)), alb.shape), scr * (1 - rust) * 0.7)
    h = 0.55 + 0.35 * rivet + 0.06 * rust * (fine - 0.5) - 0.05 * scr
    ro = _bk_mix(0.50 + 0.16 * fine, np.full((n, n), 0.90, np.float32), rust) - 0.2 * scr
    metal = np.clip(0.25 + 0.6 * scr - 0.25 * rust, 0, 1)
    return _bk_finish(alb, h, ro, metal, nstrength=2.4), (2.0, 2.0)


def _bk_m_sandbag(n, seed):
    """Burlap weave with a sag fold, one bag per tile (0.6 m x 0.3 m)."""
    u, v = _bk_grid(n)
    w1 = 0.5 + 0.5 * np.sin(u * 90.0 * 2 * math.pi)
    w2 = 0.5 + 0.5 * np.sin(v * 60.0 * 2 * math.pi)
    over = (np.floor(u * 90.0) + np.floor(v * 60.0)) % 2
    weave = np.where(over > 0, w1, w2).astype(np.float32)
    fine = _bk_noise(n, seed, 0.6, 40, None)
    cloud = _bk_noise(n, seed + 1, 2.4, 1, 6)
    fold = _bk_noise(n, seed + 2, 2.0, 2, 10, sx=0.4, sy=1.0)
    alb = _bk_col((0.50, 0.43, 0.29)) * (0.70 + 0.34 * weave[..., None]) * (0.80 + 0.30 * cloud[..., None])
    alb = alb * (0.90 + 0.2 * fold[..., None]) * (0.95 + 0.1 * fine[..., None])
    dirt = _bk_ss(0.6, 0.85, _bk_noise(n, seed + 3, 2.3, 1, 8))
    alb = _bk_mix(alb, alb * _bk_col((0.6, 0.55, 0.45)), dirt * 0.6)
    h = 0.5 + 0.18 * (weave - 0.5) + 0.25 * (fold - 0.5)
    ro = 0.92 + 0.06 * (fine - 0.5)
    return _bk_finish(alb, h, ro, nstrength=2.4), (0.6, 0.6)


def _bk_m_glass(n, seed):
    """Old window glass: mostly clear, dusty towards the glazing bars."""
    u, v = _bk_grid(n)
    cloud = _bk_noise(n, seed, 2.3, 1, 8)
    fine = _bk_noise(n, seed + 1, 1.0, 20, None)
    edge = np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v))
    dust = (1.0 - _bk_ss(0.0, 0.22, edge)) * 0.55 + 0.25 * _bk_ss(0.5, 0.8, cloud) + 0.10 * _bk_streaks(n, seed + 2)
    alb = _bk_mix(np.broadcast_to(_bk_col((0.42, 0.50, 0.52)), (n, n, 3)),
                  np.broadcast_to(_bk_col((0.50, 0.47, 0.40)), (n, n, 3)), np.clip(dust, 0, 1))
    alpha = np.clip(0.22 + 0.55 * dust + 0.05 * fine, 0.0, 0.9)
    h = 0.5 + 0.02 * (cloud - 0.5)
    ro = 0.08 + 0.5 * np.clip(dust, 0, 1)
    return _bk_finish(alb, h, ro, nstrength=0.6, alpha=alpha), (1.0, 1.0)


def _bk_m_rubble(n, seed):
    """Broken brick and mortar dust for rubble heaps and broken wall tops."""
    cells = 26
    f1, f2, cid = _bk_worley(n, cells, seed, 1.0)
    r = _bk_rng(seed + 1)
    pick = r.rand(cells * cells).astype(np.float32)[cid]
    tone = r.rand(cells * cells).astype(np.float32)[cid]
    chunk = _bk_ss(0.03, 0.12, f2 - f1)
    fine = _bk_noise(n, seed + 2, 0.7, 30, None)
    cloud = _bk_noise(n, seed + 3, 2.4, 1, 6)
    cols = _bk_palette(pick, _BK_BRICK_RED + ((0.52, 0.50, 0.46), (0.44, 0.42, 0.39), (0.40, 0.22, 0.16)))
    alb = cols * (0.7 + 0.5 * tone[..., None]) * (0.85 + 0.3 * fine[..., None])
    dust = _bk_col((0.42, 0.38, 0.33)) * (0.8 + 0.4 * fine[..., None])
    alb = _bk_mix(dust, alb, chunk)
    veil = _bk_ss(0.30, 0.70, cloud)
    alb = _bk_mix(alb, dust, 0.35 + veil * 0.45) * (0.82 + 0.24 * cloud[..., None])
    h = chunk * (0.4 + 0.5 * tone + 0.1 * (fine - 0.5)) + (1 - chunk) * 0.2
    ro = np.full((n, n), 0.93, np.float32)
    return _bk_finish(alb, h, ro, nstrength=3.6, ao_strength=1.4), (1.5, 1.5)


# name -> (maker, kwargs, pixel size, alpha mode, double sided)
_BK_MATERIALS = {
    "brick_red": (_bk_m_brick, {}, 1024),
    "brick_dark": (_bk_m_brick, {"palette": _BK_BRICK_DARK, "soot": 0.9, "mortar": (0.36, 0.34, 0.31)}, 1024),
    "brick_yellow": (_bk_m_brick, {"palette": _BK_BRICK_YELLOW, "soot": 0.7, "mortar": (0.48, 0.46, 0.42)}, 1024),
    "plaster_ochre": (_bk_m_plaster, {"color": (0.72, 0.62, 0.44), "exposed": 0.11}, 1024),
    "plaster_white": (_bk_m_plaster, {"color": (0.76, 0.74, 0.68), "exposed": 0.10, "stain": 0.8}, 1024),
    "plaster_rose": (_bk_m_plaster, {"color": (0.66, 0.50, 0.43), "exposed": 0.09}, 1024),
    "plaster_int": (_bk_m_plaster, {"color": (0.80, 0.74, 0.61), "exposed": 0.03, "stain": 0.40}, 1024),
    "plaster_green": (_bk_m_plaster, {"color": (0.46, 0.52, 0.44), "exposed": 0.05, "stain": 0.5}, 1024),
    "wallpaper": (_bk_m_wallpaper, {}, 1024),
    "wallpaper_red": (_bk_m_wallpaper, {"a": (0.46, 0.22, 0.20), "b": (0.60, 0.50, 0.38)}, 1024),
    "concrete": (_bk_m_concrete, {}, 1024),
    "concrete_floor": (_bk_m_concrete_floor, {}, 512),
    "wood_floor": (_bk_m_wood_floor, {}, 512),
    "wood_trim": (_bk_m_wood_trim, {}, 512),
    "wood_light": (_bk_m_wood_trim, {"color": (0.42, 0.31, 0.19)}, 512),
    "wood_beam": (_bk_m_wood_beam, {}, 512),
    "wood_green": (_bk_m_painted_wood, {}, 512),
    "wood_red": (_bk_m_painted_wood, {"paint": (0.40, 0.13, 0.10)}, 512),
    "wood_blue": (_bk_m_painted_wood, {"paint": (0.22, 0.30, 0.38)}, 512),
    "wood_white": (_bk_m_painted_wood, {"paint": (0.66, 0.64, 0.58)}, 512),
    "roof_tiles": (_bk_m_roof_tiles, {}, 1024),
    "roof_slate": (_bk_m_slate, {}, 1024),
    "stone": (_bk_m_stone, {}, 1024),
    "cobble": (_bk_m_cobble, {}, 512),
    "checker": (_bk_m_checker, {}, 512),
    "corrugated": (_bk_m_corrugated, {}, 512),
    "steel": (_bk_m_steel, {}, 512),
    "sandbag": (_bk_m_sandbag, {}, 256),
    "glass": (_bk_m_glass, {}, 256),
    "rubble": (_bk_m_rubble, {}, 512),
}


# metres covered by one tile of each material (needed to lay out UVs without
# generating the texture first)
_BK_TILE_BY_MAKER = {_bk_m_brick: (4.5, 3.0), _bk_m_plaster: (4.5, 3.0), _bk_m_wallpaper: (3.0, 3.0),
                     _bk_m_concrete: (4.5, 3.0), _bk_m_concrete_floor: (4.0, 4.0), _bk_m_wood_floor: (2.0, 2.0),
                     _bk_m_wood_trim: (1.5, 0.9), _bk_m_wood_beam: (1.5, 0.75), _bk_m_painted_wood: (1.2, 1.2), _bk_m_roof_tiles: (2.0, 1.6),
                     _bk_m_slate: (2.0, 1.6), _bk_m_stone: (3.0, 2.4), _bk_m_cobble: (2.4, 2.4),
                     _bk_m_checker: (2.0, 2.0), _bk_m_corrugated: (2.0, 2.0), _bk_m_steel: (2.0, 2.0),
                     _bk_m_sandbag: (0.6, 0.6), _bk_m_glass: (1.0, 1.0), _bk_m_rubble: (1.5, 1.5)}
_BK_TILE = {k: _BK_TILE_BY_MAKER[v[0]] for k, v in _BK_MATERIALS.items()}


def _bk_tex_cache_dir():
    base = os.environ.get("HEAVEN3D_CACHE") or os.path.join(os.path.expanduser("~"), ".heaven3d", "cache")
    d = os.path.join(base, "building_maker_v%d" % _BK_TEX_VERSION)
    try:
        if not os.path.isdir(d) and os.path.isdir(base):
            # textures of older versions are never used again: free the disk
            for old in os.listdir(base):
                if old.startswith("building_maker_v") and old != os.path.basename(d):
                    od = os.path.join(base, old)
                    for f in os.listdir(od):
                        if f.endswith((".png", ".json")) or ".tmp" in f:
                            os.remove(os.path.join(od, f))
                    try:
                        os.rmdir(od)
                    except OSError:
                        pass
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".w")
        open(probe, "w").close()
        os.remove(probe)
    except OSError:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "heaven3d_building_maker_v%d" % _BK_TEX_VERSION)
        os.makedirs(d, exist_ok=True)
    return d


def _bk_save_png(path, arr):
    tmp = path + ".tmp%d" % os.getpid()
    try:
        from PIL import Image
        Image.fromarray(arr).save(tmp, "PNG", compress_level=3)
    except Exception:
        if arr.ndim == 3 and arr.shape[2] == 3:
            data = _fp_rgb_to_png(arr)
        else:                                               # RGBA / grey without PIL: via pygame
            import pygame as _pg
            mode = "RGBA" if arr.ndim == 3 else "RGB"
            a = arr if arr.ndim == 3 else np.repeat(arr[..., None], 3, 2)
            surf = _pg.image.frombuffer(np.ascontiguousarray(a).tobytes(), (arr.shape[1], arr.shape[0]), mode)
            _pg.image.save(surf, tmp + ".png")
            os.replace(tmp + ".png", tmp)
            data = None
        if data is not None:
            with open(tmp, "wb") as f:
                f.write(data)
    os.replace(tmp, path)                                    # atomic: never a half-written texture


def bk_material_maps(name):
    """Generate one material.  -> ({albedo, normal, mr, ao: uint8 arrays}, (tile_w, tile_h) metres)."""
    maker, kw, size = _BK_MATERIALS[name]
    n = max(64, int(size * _BK_TEX_QUALITY[0]))
    seed = 1000 + (sum(ord(c) * (i + 7) for i, c in enumerate(name)) % 9000)
    return maker(n, seed, **kw)


_BK_PROJECT_TEX = "building_maker"          # <project>/assets/building_maker: textures that travel with the game


def _bk_project_tex_dir():
    """Where a project keeps its copies of the building textures (so an
    exported game never has to generate them), or None."""
    try:
        pp = getattr(_FP_ENGINE, "project_path", None)
    except NameError:
        pp = None
    if not pp:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(pp)), "assets", _BK_PROJECT_TEX)


def _bk_tex_set(d, name):
    """The four map paths of a material inside folder d, or None if incomplete."""
    paths = {k: os.path.join(d, "%s_%s.png" % (name, k)) for k in ("albedo", "normal", "mr", "ao")}
    return paths if all(os.path.isfile(p) for p in paths.values()) else None


def bk_material_files(name):
    """Paths of the PNGs of a material: the project's own copies when it has
    them, else the per-machine cache (made on first use).
    -> {"albedo", "normal", "mr", "ao", "tile": (w, h)}"""
    pd = _bk_project_tex_dir()
    if pd and os.path.isdir(pd):
        got = _bk_tex_set(pd, name)
        if got:
            got["tile"] = _BK_TILE[name]
            return got
    d = _bk_tex_cache_dir()
    q = int(round(_BK_TEX_QUALITY[0] * 100))
    paths = {k: os.path.join(d, "%s_q%d_%s.png" % (name, q, k)) for k in ("albedo", "normal", "mr", "ao")}
    meta = os.path.join(d, "%s_q%d.json" % (name, q))
    if all(os.path.isfile(p) for p in paths.values()) and os.path.isfile(meta):
        try:
            with open(meta) as f:
                paths["tile"] = tuple(json.load(f)["tile"])
            return paths
        except Exception:
            pass
    maps, tile = bk_material_maps(name)
    for k, p in paths.items():
        _bk_save_png(p, maps[k])
    with open(meta, "w") as f:
        json.dump({"tile": list(tile)}, f)
    paths["tile"] = tuple(tile)
    return paths


def bk_store_project_textures(material_names):
    """Copy the textures of these materials into the project (missing ones
    only), so the project folder and every game exported from it are
    complete.  -> number of files copied"""
    pd = _bk_project_tex_dir()
    if not pd or not material_names:
        return 0
    n = 0
    for name in sorted(set(material_names)):
        if name not in _BK_MATERIALS or (os.path.isdir(pd) and _bk_tex_set(pd, name)):
            continue
        try:
            src = bk_material_files(name)
            os.makedirs(pd, exist_ok=True)
            for k in ("albedo", "normal", "mr", "ao"):
                dst = os.path.join(pd, "%s_%s.png" % (name, k))
                if os.path.abspath(src[k]) != os.path.abspath(dst):
                    shutil.copyfile(src[k], dst + ".tmp")
                    os.replace(dst + ".tmp", dst)
                    n += 1
        except Exception as ex:
            Log.warn(f"[Building Maker] could not copy the '{name}' textures into the project: {ex}")
    return n

"""Tropical island generator (pure numpy, no engine dependency).

    isl = generate(params)        # params: dict, see DEFAULTS

Returns an Island with
    heights   (N, N) float32, metres, sea level = 0, grid centred on the origin
    extent    world size of the grid (m); cell = extent / (N - 1)
    splat     (S, S, 4) uint8  weights: sand, grass/jungle floor, dirt, rock
    roads     list of (K, 3) polylines (world x, y, z)
    instances {species: (M, 6) float32 = x, y, z, yaw, scale, variant}
    spawn     (x, y, z) a good player start (beach, at a road end)
"""
import heapq
import math

import numpy as np

GEN_VERSION = 7                      # bump when the output of generate() changes (invalidates caches)
TOPOLOGIES = ("classic", "volcano", "mountains", "hills", "atoll", "twin_peaks", "plateau", "archipelago", "flat")

DEFAULTS = {
    "seed": 1,
    "size": 700.0,            # island diameter (m)
    "topology": "classic",
    "max_height": 0.0,        # 0 = the topology's natural height
    "coast_roughness": 0.5,   # 0 = round, 1 = ragged with bays and capes
    "beach_width": 0.5,       # 0..1
    "erosion": 0.5,           # 0..1 (river valleys + talus)
    "roads": 3,               # dirt roads
    "road_width": 4.0,
    "jungle": 0.6,            # 0..1 share of land covered by jungle
    "tree_density": 1.0,      # multipliers
    "palm_density": 1.0,
    "grass_density": 1.0,
    "flower_density": 1.0,
    "rock_density": 1.0,
    "resolution": 0,          # heightmap cells per side (0 = auto)
}

SPECIES = ("coconut_palm", "royal_palm", "island_tree", "jacaranda", "acacia",
           "grass_tall", "grass_lush", "flower_orange", "flower_pink", "boulder", "mossy_rocks")


# --------------------------------------------------------------------------- noise
class Noise:
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.perm = {}

    def _grad(self, f, k):
        key = (f, k)
        g = self.perm.get(key)
        if g is None:
            a = self.rng.uniform(0, 2 * math.pi, (f + 2, f + 2))
            g = np.stack([np.cos(a), np.sin(a)], -1)
            self.perm[key] = g
        return g

    def perlin(self, x, y, f, k=0):
        """Gradient noise of frequency f on [0,1]^2 coordinates, ~[-0.7, 0.7]."""
        g = self._grad(f, k)
        X, Y = np.clip(x, 0.0, 1.0) * f, np.clip(y, 0.0, 1.0) * f
        xi = np.clip(np.floor(X).astype(np.int64), 0, f)
        yi = np.clip(np.floor(Y).astype(np.int64), 0, f)
        xf, yf = X - xi, Y - yi
        u = xf * xf * xf * (xf * (xf * 6 - 15) + 10)
        v = yf * yf * yf * (yf * (yf * 6 - 15) + 10)

        def dot(ix, iy, dx, dy):
            gg = g[iy, ix]
            return gg[..., 0] * dx + gg[..., 1] * dy
        n00 = dot(xi, yi, xf, yf)
        n10 = dot(xi + 1, yi, xf - 1, yf)
        n01 = dot(xi, yi + 1, xf, yf - 1)
        n11 = dot(xi + 1, yi + 1, xf - 1, yf - 1)
        a = n00 + u * (n10 - n00)
        b = n01 + u * (n11 - n01)
        return a + v * (b - a)

    def fbm(self, x, y, f0=4, octaves=6, gain=0.5, lac=2.0, k=0):
        s, amp, tot, f = 0.0, 1.0, 0.0, float(f0)
        for o in range(octaves):
            s = s + amp * self.perlin(x, y, int(round(f)), k * 31 + o)
            tot += amp
            amp *= gain
            f *= lac
        return s / tot

    def ridged(self, x, y, f0=3, octaves=6, k=0):
        s, amp, tot, f, w = 0.0, 1.0, 0.0, float(f0), 1.0
        for o in range(octaves):
            n = 1.0 - np.abs(self.perlin(x, y, int(round(f)), k * 37 + o) * 1.6)
            n = n * n * w
            w = np.clip(n * 1.8, 0.0, 1.0)
            s = s + amp * n
            tot += amp
            amp *= 0.5
            f *= 2.0
        return s / tot


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def bilinear(img, u, v):
    """Sample img (H, W[, C]) at normalised u, v in [0, 1]."""
    H, W = img.shape[:2]
    x = np.clip(u * (W - 1), 0, W - 1.0001)
    y = np.clip(v * (H - 1), 0, H - 1.0001)
    x0, y0 = x.astype(np.int64), y.astype(np.int64)
    fx, fy = x - x0, y - y0
    if img.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    a = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    b = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return a * (1 - fy) + b * fy


def blur(a, r):
    """Fast separable box blur (radius r cells, 3 passes ~ gaussian)."""
    if r < 1:
        return a
    out = a.astype(np.float64)
    for _ in range(3):
        for axis in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if ax == axis else (0, 0) for ax in range(2)], mode="edge"), axis=axis)
            if axis == 0:
                out = (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)
            else:
                out = (c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)
    return out


# --------------------------------------------------------------------------- shape
def _shape(p, nz, X, Z, R):
    """Land height field before erosion: metres, sea at 0.  X, Z in metres."""
    topo = p["topology"]
    D = p["size"] * 0.5
    rough = float(p["coast_roughness"])
    u = (X / R + 1) * 0.5
    v = (Z / R + 1) * 0.5
    # domain-warped coastline
    wx = nz.fbm(u, v, 3, 5, k=1)
    wz = nz.fbm(u, v, 3, 5, k=2)
    warp = (0.18 + 0.32 * rough) * D
    Xw, Zw = X + wx * warp, Z + wz * warp
    d = np.sqrt(Xw * Xw + Zw * Zw) / D                    # 1 = nominal coast
    coastn = nz.fbm(u, v, 4, 4, gain=0.42, k=3) * (0.12 + 0.3 * rough)
    d = d + coastn
    detail = nz.fbm(u, v, 8, 6, k=4)
    hills = nz.fbm(u, v, 4, 6, k=5)
    ridge = nz.ridged(u * 1.0 + 0.3 * wx, v + 0.3 * wz, 3, 6, k=6)
    d = d * 0.82                                           # coast lands near the requested size
    land = smoothstep(1.05, 0.55, d)                       # 0 sea .. 1 inland
    base = land * 6.0 - (1.0 - land) * 4.0                 # coastal shelf
    if topo == "classic":
        H = p["max_height"] or 95.0
        core = smoothstep(0.95, 0.05, d) ** 1.4
        h = base + core * H * (0.45 * ridge + 0.35 * (hills * 0.5 + 0.5)) + land * detail * 6
    elif topo == "volcano":
        H = p["max_height"] or 160.0
        cone = smoothstep(0.95, 0.12, d) ** 1.7
        crater = np.exp(-(d / 0.13) ** 2)
        h = base + cone * H * (0.92 + 0.12 * ridge) - crater * H * 0.42 + land * detail * 7
        h += smoothstep(0.5, 0.15, d) * ridge * H * 0.12   # lava ridges
    elif topo == "mountains":
        H = p["max_height"] or 180.0
        core = smoothstep(1.0, 0.1, d) ** 1.1
        h = base + core * H * (0.75 * ridge ** 1.3 + 0.2 * (hills * 0.5 + 0.5)) + land * detail * 8
    elif topo == "hills":
        H = p["max_height"] or 45.0
        core = smoothstep(1.0, 0.25, d)
        h = base + core * H * (0.55 + 0.45 * hills) * (0.6 + 0.4 * ridge) + land * detail * 4
    elif topo == "atoll":
        H = p["max_height"] or 9.0
        ring = np.exp(-((d - 0.78) / (0.11 + 0.06 * rough)) ** 2)
        lagoon = smoothstep(0.7, 0.2, d)
        h = ring * (H + detail * 4 + hills * 3) - 4.0 * (1 - ring) - lagoon * 4.0
        h = np.where(d > 1.15, -14.0 - (d - 1.15) * 20, h)
    elif topo == "twin_peaks":
        H = p["max_height"] or 140.0
        a = np.sqrt((Xw + D * 0.33) ** 2 + (Zw + D * 0.08) ** 2) / D
        b = np.sqrt((Xw - D * 0.36) ** 2 + (Zw - D * 0.10) ** 2) / D
        peaks = np.maximum(smoothstep(0.62, 0.0, a) ** 1.5, 0.8 * smoothstep(0.55, 0.0, b) ** 1.5)
        h = base + land * (peaks * H * (0.8 + 0.25 * ridge) + 14 * (hills * 0.5 + 0.5)) + land * detail * 6
    elif topo == "plateau":
        H = p["max_height"] or 70.0
        mesa = smoothstep(0.72, 0.6, d + 0.1 * hills)
        terr = np.floor(mesa * 3.0 + 0.5) / 3.0
        terr = terr * 0.7 + mesa * 0.3
        h = base + land * (terr * H + 10 * (hills * 0.5 + 0.5)) + land * detail * 5
    elif topo == "archipelago":
        H = p["max_height"] or 60.0
        rng = np.random.default_rng(int(p["seed"]) + 99)
        h = np.full(X.shape, -12.0)
        n = 4 + int(rng.integers(0, 3))
        a0 = rng.uniform(0, 2 * math.pi)
        for i in range(n):
            ang = a0 + i * 2 * math.pi / max(1, n - 1) + rng.uniform(-0.4, 0.4)
            rad = rng.uniform(0.5, 0.78) * D if i else rng.uniform(0.0, 0.15) * D
            cx, cz = math.cos(ang) * rad, math.sin(ang) * rad
            r = D * (rng.uniform(0.38, 0.5) if i == 0 else rng.uniform(0.2, 0.34))
            dd = np.sqrt((Xw - cx) ** 2 + (Zw - cz) ** 2) / r + coastn * 0.5
            l = smoothstep(1.05, 0.55, dd)
            hh = l * 6 - (1 - l) * 4 + smoothstep(0.9, 0.0, dd) ** 1.5 * H * rng.uniform(0.35, 1.0) * (0.6 + 0.5 * ridge)
            h = np.maximum(h, hh + l * detail * 5)
    else:  # flat
        H = p["max_height"] or 12.0
        core = smoothstep(1.0, 0.3, d)
        h = base + core * H * (0.5 + 0.5 * hills) + land * detail * 2.5
    # deep ocean floor away from the island
    far = smoothstep(1.1, 1.7, d)
    h = h * (1 - far) + far * (-22.0 + detail * 3)
    return h


def _thermal(h, cell, iters, talus_deg=38.0):
    t = math.tan(math.radians(talus_deg)) * cell
    for _ in range(iters):
        lap = np.zeros_like(h)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb = np.roll(np.roll(h, dy, 0), dx, 1)
            diff = h - nb
            move = np.where(diff > t, (diff - t) * 0.12, 0.0)
            lap -= move
            lap += np.roll(np.roll(move, -dy, 0), -dx, 1)
        h = h + lap
    return h


def _flow_erosion(h, cell, amount):
    """Carves drainage valleys: D8 flow accumulation (coarse grid), then cut
    proportional to sqrt(area) * slope.  Python loop over cells ~0.3 s at 192^2."""
    N = h.shape[0]
    M = min(N, 192)
    hs = bilinear(h, *np.meshgrid(np.linspace(0, 1, M), np.linspace(0, 1, M)))
    order = np.argsort(-hs, axis=None)
    acc = np.ones(M * M)
    flat = hs.reshape(-1)
    nbrs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for idx in order:
        if flat[idx] <= 0.0:
            continue
        y, x = divmod(int(idx), M)
        best, bh = -1, flat[idx]
        for dy, dx in nbrs:
            yy, xx = y + dy, x + dx
            if 0 <= yy < M and 0 <= xx < M:
                j = yy * M + xx
                if flat[j] < bh:
                    bh, best = flat[j], j
        if best >= 0:
            acc[best] += acc[idx]
    A = acc.reshape(M, M)
    gy, gx = np.gradient(hs, cell * (N - 1) / (M - 1))
    slope = np.sqrt(gx * gx + gy * gy)
    cut = np.sqrt(A) * np.clip(slope, 0, 1.5) * (hs > 0.5)
    cut = blur(cut, 1)
    cut = cut / max(np.percentile(cut, 99.5), 1e-6)
    cut = np.clip(cut, 0, 1.3) * amount * 9.0
    full = bilinear(cut, *np.meshgrid(np.linspace(0, 1, N), np.linspace(0, 1, N)))
    return h - full


def _beach(h, width):
    """Flattened beach profile around sea level."""
    top = 1.2 + 2.6 * width
    t = np.clip(h / top, -1, 1)
    flat = np.where((h > -2.5) & (h < top), np.sign(t) * np.abs(t) ** 1.7 * top * 0.85, h)
    w = smoothstep(top * 1.6, top, h) * smoothstep(-4.0, -1.0, h)
    return h * (1 - w) + flat * w


# --------------------------------------------------------------------------- roads
def _astar(cost, start, goal):
    M = cost.shape[0]
    INF = 1e30
    g = np.full(cost.shape, INF)
    g[start] = 0.0
    came = {}
    pq = [(0.0, start)]
    gy, gx = goal
    nb = [(-1, -1, 1.414), (-1, 0, 1), (-1, 1, 1.414), (0, -1, 1), (0, 1, 1), (1, -1, 1.414), (1, 0, 1), (1, 1, 1.414)]
    while pq:
        f, cur = heapq.heappop(pq)
        if cur == goal:
            break
        y, x = cur
        gc = g[cur]
        if f - math.hypot(y - gy, x - gx) > gc + 1e-6:
            continue
        for dy, dx, w in nb:
            yy, xx = y + dy, x + dx
            if 0 <= yy < M and 0 <= xx < M:
                c = cost[yy, xx]
                if c >= INF:
                    continue
                ng = gc + w * (c + cost[y, x]) * 0.5
                if ng < g[yy, xx]:
                    g[yy, xx] = ng
                    came[(yy, xx)] = cur
                    heapq.heappush(pq, (ng + math.hypot(yy - gy, xx - gx), (yy, xx)))
    if goal not in came and goal != start:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(came[path[-1]])
    return path[::-1]


def _chaikin(pts, it=3):
    for _ in range(it):
        if len(pts) < 3:
            return pts
        q = pts[:-1] * 0.75 + pts[1:] * 0.25
        r = pts[:-1] * 0.25 + pts[1:] * 0.75
        mid = np.empty((len(q) * 2, pts.shape[1]))
        mid[0::2], mid[1::2] = q, r
        pts = np.vstack([pts[:1], mid, pts[-1:]])
    return pts


def _roads(p, h, extent, rng):
    """Dirt roads linking beach landings, inland clearings and a lookout."""
    n_roads = int(p["roads"])
    if n_roads <= 0:
        return []
    N = h.shape[0]
    M = 128
    hs = bilinear(h, *np.meshgrid(np.linspace(0, 1, M), np.linspace(0, 1, M)))
    cellc = extent / (M - 1)
    gy, gx = np.gradient(hs, cellc)
    slope = np.sqrt(gx * gx + gy * gy)
    cost = 1.0 + (slope * 7.0) ** 2
    cost[hs < 1.2] = 1e30                        # no driving through the sea
    cost[(hs >= 1.2) & (hs < 2.6)] *= 3.0        # avoid the wet beach
    land = (hs > 3.0) & (slope < 0.35)
    ys, xs = np.nonzero(land)
    if len(ys) < 10:
        return []
    # beach landings: land cells close to the coast
    coast = (hs > 2.5) & (hs < 6.0) & (slope < 0.25)
    cy, cx = np.nonzero(coast)
    pts = []
    if len(cy):
        k = rng.choice(len(cy), size=min(len(cy), 300), replace=False)
        cand = np.stack([cy[k], cx[k]], 1)
        chosen = [cand[0]]
        for c in cand[1:]:
            if min(np.hypot(*(c - q)) for q in chosen) > M * 0.35:
                chosen.append(c)
            if len(chosen) >= max(2, (n_roads + 1) // 2 + 1):
                break
        pts += [tuple(c) for c in chosen]
    # inland clearings and the lookout (highest walkable point)
    inland = land & (hs > np.percentile(hs[land], 40))
    iy, ix = np.nonzero(inland)
    if len(iy):
        k = rng.choice(len(iy), size=min(len(iy), 300), replace=False)
        for c in np.stack([iy[k], ix[k]], 1):
            if all(np.hypot(*(c - np.array(q))) > M * 0.25 for q in pts):
                pts.append(tuple(c))
            if len(pts) >= n_roads + 2:
                break
    walk = np.where(slope < 0.45, hs, -1e9)
    pts.append(tuple(np.array(np.unravel_index(np.argmax(walk), walk.shape))))
    pts = list(dict.fromkeys(pts))
    # minimum spanning tree over the points (Prim), at most n_roads edges + extra loop
    P = np.array(pts, float)
    used = {0}
    edges = []
    while len(used) < len(P) and len(edges) < n_roads:
        best = None
        for i in used:
            for j in range(len(P)):
                if j in used:
                    continue
                d = np.hypot(*(P[i] - P[j]))
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            break
        edges.append((best[1], best[2]))
        used.add(best[2])
    roads = []
    for i, j in edges:
        path = _astar(cost, tuple(int(a) for a in P[i]), tuple(int(a) for a in P[j]))
        if not path or len(path) < 4:
            continue
        a = np.array(path, float)
        a = _chaikin(a[::2] if len(a) > 8 else a, 3)
        wx = a[:, 1] / (M - 1) * extent - extent / 2
        wz = a[:, 0] / (M - 1) * extent - extent / 2
        # resample every ~2 m
        seg = np.hypot(np.diff(wx), np.diff(wz))
        s = np.concatenate([[0], np.cumsum(seg)])
        ss = np.arange(0, s[-1], 2.0)
        roads.append(np.stack([np.interp(ss, s, wx), np.interp(ss, s, wz)], 1))
    return roads


def _nearest_road(pts, vals, N, extent, maxd):
    """Distance (m) from every grid point to the nearest road point within maxd,
    and that point's value (numpy only: each point updates a small window)."""
    cell = extent / (N - 1)
    dist = np.full((N, N), np.inf)
    val = np.zeros((N, N))
    r = int(math.ceil(maxd / cell)) + 1
    off = np.arange(-r, r + 1)
    for (px, pz), pv in zip(pts, vals):
        cx = int(round((px + extent / 2) / cell))
        cz = int(round((pz + extent / 2) / cell))
        x0, x1 = max(0, cx - r), min(N, cx + r + 1)
        z0, z1 = max(0, cz - r), min(N, cz + r + 1)
        if x0 >= x1 or z0 >= z1:
            continue
        gx = (np.arange(x0, x1) * cell - extent / 2) - px
        gz = (np.arange(z0, z1) * cell - extent / 2) - pz
        d = np.sqrt(gz[:, None] ** 2 + gx[None, :] ** 2)
        sub = dist[z0:z1, x0:x1]
        better = (d < sub) & (d <= maxd)
        sub[better] = d[better]
        val[z0:z1, x0:x1][better] = pv
    return dist, val


def _road_field(roads, N, extent, width):
    """Distance (m) from every grid point to the nearest road."""
    if not roads:
        return np.full((N, N), 1e9), None
    pts = np.vstack(roads)
    d, _ = _nearest_road(pts, np.zeros(len(pts)), N, extent, width * 6 + 30)
    return np.where(np.isfinite(d), d, 1e9), pts


# --------------------------------------------------------------------------- island
class Island:
    pass


def generate(params=None, progress=None):
    p = dict(DEFAULTS)
    p.update(params or {})
    p["size"] = float(min(max(p["size"], 150.0), 3000.0))
    if p["topology"] not in TOPOLOGIES:
        p["topology"] = "classic"
    seed = int(p["seed"])
    rng = np.random.default_rng(seed)
    nz = Noise(seed)
    extent = p["size"] * 1.45
    N = int(p["resolution"]) or int(min(513, max(257, round(extent / 1.6))))
    N = N | 1
    cell = extent / (N - 1)
    xs = np.linspace(-extent / 2, extent / 2, N)
    X, Z = np.meshgrid(xs, xs)
    if progress:
        progress("shape")
    h = _shape(p, nz, X, Z, extent / 2)
    er = float(p["erosion"])
    if er > 0:
        if progress:
            progress("erosion")
        h = _flow_erosion(h, cell, er)
        h = _thermal(h, cell, int(10 + 25 * er))
    h = _beach(h, float(p["beach_width"]))
    # roads: plan, then carve a smooth bed into the terrain
    if progress:
        progress("roads")
    roads2d = _roads(p, h, extent, rng)
    rw = float(p["road_width"])
    road_d, road_pts = _road_field(roads2d, N, extent, rw)
    roads = []
    if road_pts is not None:
        u = (road_pts[:, 0] + extent / 2) / extent
        v = (road_pts[:, 1] + extent / 2) / extent
        hb = blur(h, max(1, int(round(rw * 1.5 / cell))))
        ry = bilinear(hb, u, v)
        # smooth the road's grade along each road
        out, k = [], 0
        for r in roads2d:
            y = ry[k:k + len(r)]
            k += len(r)
            y = np.convolve(np.pad(y, 6, mode="edge"), np.ones(13) / 13, mode="valid")
            y = np.maximum(y, 1.6)
            out.append(np.column_stack([r[:, 0], y, r[:, 1]]))
        roads = out
        allp = np.vstack(out)
        dd, lv = _nearest_road(allp[:, [0, 2]], allp[:, 1], N, extent, rw * 4 + 12)
        lvl = np.where(np.isfinite(dd), lv, h)
        w = smoothstep(rw * 2.2 + 3.0, rw * 0.55, dd)
        h = h * (1 - w) + (lvl - 0.08) * w
        road_d = np.where(np.isfinite(dd), dd, 1e9)
    # final maps
    gy, gx = np.gradient(h, cell)
    slope = np.sqrt(gx * gx + gy * gy)                     # tan(angle)
    hmax = max(float(h.max()), 1.0)
    u01 = (X / extent + 0.5)
    v01 = (Z / extent + 0.5)
    n1 = nz.fbm(u01, v01, 9, 5, k=11)
    n2 = nz.fbm(u01, v01, 22, 4, k=12)
    jungle_n = nz.fbm(u01, v01, 5, 5, k=13)
    if progress:
        progress("materials")
    beach_top = 1.6 + 2.8 * float(p["beach_width"])
    sand = smoothstep(beach_top + 1.2 + n1 * 1.5, beach_top - 0.6, h)
    rock = smoothstep(0.9, 1.3, slope + n2 * 0.18)
    rock = np.maximum(rock, smoothstep(hmax * 0.94, hmax * 1.02, h) * smoothstep(0.35, 0.6, slope))
    road = smoothstep(rw * 0.8 + 0.6, rw * 0.35, road_d + n2 * 0.9)
    dirt = np.clip(road + smoothstep(0.35, 0.75, n2 * 0.6 + n1 * 0.6) * 0.45 * (1 - sand) + smoothstep(0.42, 0.6, slope) * 0.35, 0, 1)
    dirt = np.maximum(dirt, sand * (1 - sand) * 1.2)       # dirty band at the top of the beach
    grass = np.ones_like(h)
    # layer priority: rock > road > sand > dirt > grass
    w_rock = rock * (1 - road)
    w_sand = sand * (1 - w_rock) * (1 - road)
    w_dirt = np.clip(dirt * (1 - w_rock) * (1 - w_sand) + road * (1 - w_rock), 0, 1)
    w_grass = grass * np.clip(1 - w_rock - w_sand - w_dirt, 0, 1)
    W = np.stack([w_sand, w_grass, w_dirt, w_rock], -1)
    W = W / np.maximum(W.sum(-1, keepdims=True), 1e-6)
    S = 1024 if N > 300 else 512
    su = np.linspace(0, 1, S)
    SU, SV = np.meshgrid(su, su)
    splat = bilinear(W.astype(np.float32), SU, SV)
    splat = (np.clip(splat, 0, 1) * 255 + 0.5).astype(np.uint8)
    # ---------------- scatter
    if progress:
        progress("vegetation")
    land = (h > 0.5).astype(np.float32)
    jungle_amt = float(p["jungle"])
    jthr = 0.25 - jungle_amt * 0.6
    jungle = smoothstep(jthr - 0.06, jthr + 0.08, jungle_n) * (1 - smoothstep(0.85, 1.2, slope)) \
        * smoothstep(beach_top + 0.5, beach_top + 5.0, h) * (1 - smoothstep(hmax * 0.8, hmax * 0.97, h))
    near_road = smoothstep(rw * 0.75 + 4.0, rw * 0.6 + 1.0, road_d)
    jungle *= (1 - near_road)
    coastal = smoothstep(beach_top - 0.8, beach_top + 0.4, h) * smoothstep(beach_top + 16.0, beach_top + 3.0, h)
    open_land = land * (1 - jungle) * (1 - w_rock) * smoothstep(beach_top - 0.2, beach_top + 1.5, h)
    meadow = open_land * (1 - near_road) * smoothstep(0.8, 0.45, slope)
    maps = {"jungle": jungle, "coastal": coastal * (1 - near_road) * land, "meadow": meadow,
            "slope": slope, "rock": w_rock, "sand": w_sand, "h": h, "road": near_road,
            "flowers": meadow * smoothstep(0.05, 0.35, nz.fbm(u01, v01, 14, 3, k=21))}
    td, pd, gd, fd, rd = (float(p[k]) for k in ("tree_density", "palm_density", "grass_density", "flower_density", "rock_density"))
    area_scale = (p["size"] / 700.0) ** 2
    rules = [
        # species, spacing m, probability expression, scale range, variants, max slope
        ("coconut_palm", 7.5 / math.sqrt(max(pd, 0.05)), lambda m: m["coastal"] * 0.9 + m["jungle"] * 0.18, (0.8, 1.2), 5, 0.8),
        ("royal_palm", 13.0 / math.sqrt(max(pd, 0.05)), lambda m: m["jungle"] * 0.35 + m["coastal"] * 0.15, (0.85, 1.15), 1, 0.75),
        ("island_tree", 7.0 / math.sqrt(max(td, 0.05)), lambda m: m["jungle"] * 0.55 + m["meadow"] * 0.025, (0.75, 1.3), 1, 0.95),
        ("jacaranda", 15.0 / math.sqrt(max(td, 0.05)), lambda m: m["jungle"] * 0.45 + m["meadow"] * 0.03, (0.8, 1.25), 1, 0.85),
        ("acacia", 9.0 / math.sqrt(max(td, 0.05)), lambda m: m["jungle"] * 0.4 + m["meadow"] * 0.06, (0.8, 1.3), 1, 0.9),
        ("grass_tall", 1.15 / math.sqrt(max(gd, 0.05)), lambda m: (m["meadow"] * 0.8 + m["jungle"] * 0.35) * (1 - m["road"] * 0.7), (1.8, 3.2), 5, 0.75),
        ("grass_lush", 0.6 / math.sqrt(max(gd, 0.05)), lambda m: (m["meadow"] * 0.9 + m["jungle"] * 0.6 + m["coastal"] * 0.3) * (1 - m["road"] * 0.6), (1.8, 3.4), 6, 0.75),
        ("flower_orange", 2.4 / math.sqrt(max(fd, 0.05)), lambda m: m["flowers"] * 0.9 + m["coastal"] * 0.05, (1.5, 2.6), 8, 0.6),
        ("flower_pink", 3.0 / math.sqrt(max(fd, 0.05)), lambda m: m["flowers"] * 0.7 + m["jungle"] * 0.06, (1.5, 2.4), 6, 0.6),
        ("boulder", 26.0 / math.sqrt(max(rd, 0.05)), lambda m: m["rock"] * 0.6 + smoothstep(0.3, 0.6, m["slope"]) * 0.35 + m["meadow"] * 0.02 + m["sand"] * 0.04, (0.5, 2.2), 1, 3.0),
        ("mossy_rocks", 14.0 / math.sqrt(max(rd, 0.05)), lambda m: m["jungle"] * 0.12 + m["rock"] * 0.3 + m["coastal"] * 0.05, (0.35, 1.0), 6, 3.0),
    ]
    inst = {}
    for name, spacing, prob, (s0, s1), nvar, maxs in rules:
        n = int((extent / spacing) ** 2)
        if n <= 0:
            inst[name] = np.zeros((0, 6), np.float32)
            continue
        if n > 2_500_000:
            n = 2_500_000
        g = int(math.sqrt(n))
        jj, ii = np.meshgrid(np.arange(g), np.arange(g))
        uu = (jj.ravel() + rng.random(g * g)) / g
        vv = (ii.ravel() + rng.random(g * g)) / g
        sub = {k: bilinear(v, uu, vv) for k, v in maps.items()}
        pr = np.clip(prob(sub), 0, 1)
        keep = (rng.random(len(uu)) < pr) & (sub["h"] > 0.4) & (sub["slope"] < maxs)
        uu, vv = uu[keep], vv[keep]
        y = bilinear(h, uu, vv)
        x = uu * extent - extent / 2
        z = vv * extent - extent / 2
        yaw = rng.uniform(0, 2 * math.pi, len(x))
        sc = rng.uniform(s0, s1, len(x))
        var = rng.integers(0, nvar, len(x)).astype(np.float32)
        inst[name] = np.column_stack([x, y, z, yaw, sc, var]).astype(np.float32)
    # spawn: the first road end on the beach side, else the lowest dry spot facing the sea
    spawn = None
    for r in roads:
        a, b = r[0], r[-1]
        e = a if a[1] < b[1] else b
        spawn = (float(e[0]), float(e[1]) + 1.0, float(e[2]))
        break
    if spawn is None:
        cand = np.argwhere((h > beach_top) & (h < beach_top + 3) & (slope < 0.2))
        if len(cand):
            iy, ix = cand[len(cand) // 2]
        else:
            iy, ix = np.unravel_index(np.argmax(h), h.shape)
        spawn = (float(xs[ix]), float(h[iy, ix]) + 1.0, float(xs[iy]))
    isl = Island()
    isl.params = p
    isl.heights = h.astype(np.float32)
    isl.extent = float(extent)
    isl.cell = float(cell)
    isl.splat = splat
    isl.roads = roads
    isl.instances = inst
    isl.spawn = spawn
    isl.sea_level = 0.0
    isl.beach_top = beach_top
    return isl


def height_at(isl, x, z):
    u = (np.asarray(x) + isl.extent / 2) / isl.extent
    v = (np.asarray(z) + isl.extent / 2) / isl.extent
    return bilinear(isl.heights, u, v)

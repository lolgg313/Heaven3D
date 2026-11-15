"""FarScream map layout: where the camps, villages, wrecks and loot go.

Pure numpy (no engine), deterministic for a seed, so the same island
always gets the same map.  make_layout() reads a TropicalIsland
(tropical_islands.island_gen.Island) and returns a plain dict; apply_pads()
flattens the ground of every site, paints it as trodden dirt and clears the
trees and grass off it.

Coordinates are the island's own (metres, island centre at 0, 0).
"""
import math
import random

import numpy as np

try:
    from scipy import ndimage
except Exception:                       # (no scipy: a slower numpy fallback below)
    ndimage = None

# The side of each model that is its front, in the model's own frame.
FRONT = {"Bunker_A": "+x", "Bunker_B": "+z", "Bunker_C": "+z", "Tent": "+x", "Chair": "+z",
         "Table": "+z", "M3S": "+x", "pickup_healthy": "+x", "M3S_Wreck": "+x", "pickup_wreck": "+x",
         "soviet_radio_station": "+z", "SupplyCrate": "+z", "Campfire": "+z", "Li2": "+x",
         "Barrier": "+z", "CargoShip": "+x"}

TREES = ("coconut_palm", "royal_palm", "island_tree", "jacaranda", "acacia")
SMALL = ("grass_tall", "grass_lush", "flower_orange", "flower_pink", "mossy_rocks", "boulder")


def yaw_facing(model, dx, dz):
    """The node yaw (degrees) that turns the model's front towards (dx, dz)."""
    if abs(dx) + abs(dz) < 1e-9:
        return 0.0
    if FRONT.get(model, "+z") == "+x":
        return math.degrees(math.atan2(-dz, dx))
    return math.degrees(math.atan2(dx, dz))


# --------------------------------------------------------------------- maps
def _range_filter(h, k):
    if ndimage is not None:
        return ndimage.maximum_filter(h, size=k) - ndimage.minimum_filter(h, size=k)
    r = k // 2
    pad = np.pad(h, r, mode="edge")
    mx = np.full_like(h, -np.inf)
    mn = np.full_like(h, np.inf)
    for dy in range(k):
        for dx in range(k):
            s = pad[dy:dy + h.shape[0], dx:dx + h.shape[1]]
            mx = np.maximum(mx, s)
            mn = np.minimum(mn, s)
    return mx - mn


def _dist_field(mask, cell):
    if ndimage is not None:
        return ndimage.distance_transform_edt(~mask) * cell
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return np.full(mask.shape, 1e9)
    gy, gx = np.mgrid[0:mask.shape[0], 0:mask.shape[1]]
    d = np.full(mask.shape, np.inf)
    for y, x in zip(ys[::4], xs[::4]):
        d = np.minimum(d, np.hypot(gy - y, gx - x))
    return d * cell


class _Grid:
    def __init__(self, isl):
        self.h = np.asarray(isl.heights, np.float64)
        self.N = self.h.shape[0]
        self.ext = float(isl.extent)
        self.cell = self.ext / (self.N - 1)
        self.beach_top = float(isl.beach_top)

    def ij(self, x, z):
        return (int(round((z + self.ext / 2) / self.cell)), int(round((x + self.ext / 2) / self.cell)))

    def xz(self, i, j):
        return (j * self.cell - self.ext / 2, i * self.cell - self.ext / 2)

    def height(self, x, z):
        u = (x + self.ext / 2) / self.cell
        v = (z + self.ext / 2) / self.cell
        j0 = int(np.clip(math.floor(u), 0, self.N - 2))
        i0 = int(np.clip(math.floor(v), 0, self.N - 2))
        fu, fv = min(max(u - j0, 0.0), 1.0), min(max(v - i0, 0.0), 1.0)
        h = self.h
        return float((h[i0, j0] * (1 - fu) + h[i0, j0 + 1] * fu) * (1 - fv)
                     + (h[i0 + 1, j0] * (1 - fu) + h[i0 + 1, j0 + 1] * fu) * fv)

    def kernel(self, metres):
        return max(3, int(round(metres / self.cell)) * 2 + 1)


def _pick(score, mask, count, min_sep_cells, taken, rng, jitter=0.0):
    """Greedy: best scores first, at least min_sep_cells from every taken site."""
    s = np.where(mask, score, -np.inf)
    if jitter:
        s = s + rng.random(s.shape) * jitter
    flat = np.argsort(s, axis=None)[::-1]
    out = []
    W = s.shape[1]
    for k in flat[:200000]:
        if not np.isfinite(s.flat[k]):
            break
        i, j = divmod(int(k), W)
        if all((i - a) ** 2 + (j - b) ** 2 >= min_sep_cells ** 2 for a, b in taken + out):
            out.append((i, j))
            if len(out) >= count:
                break
    return out


# --------------------------------------------------------------------- layout
def make_layout(isl, seed, camps=3, villages=2):
    rng = np.random.default_rng(int(seed) * 7919 + 17)
    prng = random.Random(int(seed) * 104729 + 3)
    g = _Grid(isl)
    h, cell = g.h, g.cell
    gy, gx = np.gradient(h, cell)
    slope = np.hypot(gx, gy)
    land = h > g.beach_top + 1.2
    # roads
    road = np.zeros_like(land)
    road_pts = np.vstack(isl.roads).astype(np.float64) if isl.roads else np.zeros((0, 2))
    if road_pts.shape[1] == 3:                       # (x, height, z) points
        road_pts = road_pts[:, [0, 2]]
    for x, z in road_pts:
        i, j = g.ij(x, z)
        if 0 <= i < g.N and 0 <= j < g.N:
            road[i, j] = True
    road_d = _dist_field(road, cell) if road.any() else np.full(h.shape, 1e9)
    # how much jungle is around (trees per cell, blurred over ~30 m)
    dens = np.zeros_like(h)
    for sp in TREES:
        inst = isl.instances.get(sp)
        if inst is None or not len(inst):
            continue
        ii = np.clip(((inst[:, 2] + g.ext / 2) / cell).round().astype(int), 0, g.N - 1)
        jj = np.clip(((inst[:, 0] + g.ext / 2) / cell).round().astype(int), 0, g.N - 1)
        np.add.at(dens, (ii, jj), 1.0)
    if ndimage is not None:
        dens = ndimage.uniform_filter(dens, size=g.kernel(15))
    dens /= max(float(dens.max()), 1e-6)
    sx, sy, sz = isl.spawn
    si, sj = g.ij(sx, sz)
    gi, gj = np.mgrid[0:g.N, 0:g.N]
    spawn_d = np.hypot(gi - si, gj - sj) * cell
    edge = np.minimum(np.minimum(gi, gj), np.minimum(g.N - 1 - gi, g.N - 1 - gj)) * cell
    inland = land & (edge > 30)
    out = {"seed": int(seed), "sites": [], "items": [], "posts": [], "loot": [], "pads": [],
           "vehicles": [], "start": None}
    taken = []

    def add_site(kind, i, j, r, name):
        x, z = g.xz(i, j)
        k = g.kernel(r)
        r0, r1 = max(0, i - k // 2), min(g.N, i + k // 2 + 1)
        c0, c1 = max(0, j - k // 2), min(g.N, j + k // 2 + 1)
        target = float(np.median(h[r0:r1, c0:c1]))
        site = {"kind": kind, "name": name, "x": x, "z": z, "r": r, "y": target, "index": len(out["sites"])}
        out["sites"].append(site)
        out["pads"].append((x, z, r, target))
        taken.append((i, j))
        return site

    # ---- military camps: flat ground beside a road, away from the landing
    R20 = _range_filter(h, g.kernel(20))
    score = -R20 * 2.0 - np.abs(road_d - 24.0) * 0.12 + slope * -3.0
    got = []
    for rr, rd0, rd1, sd, sep in ((5.0, 14, 55, 140, 150), (7.0, 10, 80, 110, 130), (10.0, 0, 1e9, 90, 110),
                                  (14.0, 0, 1e9, 70, 90)):
        mask = inland & (R20 < rr) & (road_d > rd0) & (road_d < rd1) & (spawn_d > sd)
        got += _pick(score, mask, camps - len(got), int(sep / cell), taken + got, rng, 0.5)
        if len(got) >= camps:
            break
    for k, (i, j) in enumerate(got):
        add_site("camp", i, j, 21.0, "Camp %s" % "ABCDEFG"[k])
    # ---- the radio outpost: the highest flat spot
    R10 = _range_filter(h, g.kernel(10))
    mask = inland & (R10 < 6.0)
    score = h - R10 * 2.0
    for i, j in _pick(score, mask, 1, int(90 / cell), taken, rng):
        add_site("radio", i, j, 12.0, "Radio Outpost")
    # ---- islander villages: deep in the jungle, off the roads
    R15 = _range_filter(h, g.kernel(15))
    score = dens * 4.0 - R15 * 0.8
    mask = inland & (R15 < 5.0) & (road_d > 30) & (spawn_d > 110)
    if mask.sum() < 2:
        mask = inland & (R15 < 7.0) & (spawn_d > 80)
    for k, (i, j) in enumerate(_pick(score, mask, villages, int(130 / cell), taken, rng, 0.3)):
        add_site("village", i, j, 15.0, "Village %d" % (k + 1))
    # ---- the plane crash: a jungle clearing
    R18 = _range_filter(h, g.kernel(18))
    score = dens * 2.0 - R18 * 0.5
    mask = inland & (R18 < 7.0) & (road_d > 20)
    for i, j in _pick(score, mask, 1, int(110 / cell), taken, rng, 0.4):
        add_site("crash", i, j, 17.0, "Plane Crash")
    # ---- the shipwreck: a shore far from the landing
    shore = (h > -0.8) & (h < 0.9) & (spawn_d > 160) & (edge > 40)
    ship = None
    for i, j in _pick(-np.abs(h), shore, 1, int(100 / cell), taken, rng, 0.2):
        x, z = g.xz(i, j)
        n = np.array([-gx[i, j], -gy[i, j]])            # downhill = towards the sea
        if np.linalg.norm(n) < 1e-6:
            n = np.array([x, z])
        n = n / max(np.linalg.norm(n), 1e-9)
        # out to sea until the water is about 4 m deep: there it ran aground
        for d in range(12, 80, 2):
            px, pz = x + n[0] * d, z + n[1] * d
            if g.height(px, pz) < -4.0:
                break
        seabed = g.height(px, pz)
        tangent = (-n[1], n[0])
        a_ = math.radians(prng.choice((-28.0, 28.0)))     # bow towards the beach, at an angle
        tangent = (tangent[0] * math.cos(a_) - tangent[1] * math.sin(a_),
                   tangent[0] * math.sin(a_) + tangent[1] * math.cos(a_))
        ship = {"x": px, "z": pz, "shore": (x, z), "out": (float(n[0]), float(n[1]))}
        site = {"kind": "ship", "name": "Shipwreck", "x": x, "z": z, "r": 10.0, "y": g.height(x, z),
                "index": len(out["sites"])}
        out["sites"].append(site)
        taken.append((i, j))
        out["items"].append({"model": "CargoShip", "x": px, "z": pz, "y": float(seabed) - 1.2,
                             "yaw": yaw_facing("CargoShip", tangent[0], tangent[1]), "pitch": 1.5, "roll": 9.0,
                             "collider": "mesh", "site": site["index"]})
        # crates and drums washed up on the beach
        for k in range(4):
            a = prng.uniform(-1, 1)
            bx = x - n[0] * prng.uniform(2.0, 6.0) + tangent[0] * a * 9.0
            bz = z - n[1] * prng.uniform(2.0, 6.0) + tangent[1] * a * 9.0
            model = "SupplyCrate" if k < 2 else "Barrel_Yellow_Damaged"
            it = {"model": model, "x": bx, "z": bz, "yaw": prng.uniform(0, 360), "roll": prng.uniform(-8, 8),
                  "collider": "box", "site": site["index"], "tilt": True}
            if model == "SupplyCrate":
                it["role"] = "crate"
                it["loot"] = {"ammo": 30, "heal": 40} if k == 0 else {"ammo": 21}
            out["items"].append(it)
        break

    # ---- furnish every site
    def frame(site):
        """forward = towards the nearest road (or downhill), right = 90 deg to it."""
        i, j = g.ij(site["x"], site["z"])
        best = None
        if len(road_pts):
            d = np.hypot(road_pts[:, 0] - site["x"], road_pts[:, 1] - site["z"])
            k = int(np.argmin(d))
            if d[k] < 90:
                best = (road_pts[k, 0] - site["x"], road_pts[k, 1] - site["z"])
        if best is None:
            best = (-site["x"], -site["z"])                # towards the island centre
        f = np.array(best, float)
        f /= max(np.linalg.norm(f), 1e-9)
        return f, np.array([-f[1], f[0]])

    def put(site, model, u, v, face=None, collider="box", **kw):
        """A model at (u along right, v along forward) of the site, its front
        towards face (local u, v), or towards the site centre."""
        f, r = site["_f"], site["_r"]
        x = site["x"] + r[0] * u + f[0] * v
        z = site["z"] + r[1] * u + f[1] * v
        if face is None:
            fu, fv = -u, -v
        else:
            fu, fv = face
        dx, dz = r[0] * fu + f[0] * fv, r[1] * fu + f[1] * fv
        it = {"model": model, "x": float(x), "z": float(z), "yaw": yaw_facing(model, dx, dz) + kw.pop("turn", 0.0),
              "collider": collider, "site": site["index"]}
        it.update(kw)
        out["items"].append(it)
        return it

    def post(site, u, v, kind, weapon=None, face=None):
        f, r = site["_f"], site["_r"]
        x = site["x"] + r[0] * u + f[0] * v
        z = site["z"] + r[1] * u + f[1] * v
        fu, fv = face if face is not None else (u, v)
        dx, dz = r[0] * fu + f[0] * fv, r[1] * fu + f[1] * fv
        out["posts"].append({"kind": kind, "x": float(x), "z": float(z),
                             "yaw": math.degrees(math.atan2(-dx, -dz)), "weapon": weapon, "site": site["index"],
                             "seed": int(prng.randrange(1 << 30))})

    def barrels(site, u, v, n_yellow, n_red, lid=True):
        k = 0
        for kind, n in (("Barrel_Yellow_Damaged", n_yellow), ("Barrel_Explosive_Red", n_red)):
            for _ in range(n):
                a = k * 2.4 + prng.uniform(-0.3, 0.3)
                rr = 0.0 if k == 0 else 0.68
                it = put(site, kind, u + math.cos(a) * rr, v + math.sin(a) * rr, face=(1, 0),
                         turn=prng.uniform(0, 360))
                if kind == "Barrel_Explosive_Red":
                    it["role"] = "explosive"
                k += 1
        if lid:
            put(site, "Barrel_Yellow_Lid", u + 1.1, v - 0.4, face=(1, 0), collider=None, turn=prng.uniform(0, 360))

    def chairs_round(site, cu, cv, rad, n, start):
        for k in range(n):
            a = start + k * (2 * math.pi / n) + prng.uniform(-0.15, 0.15)
            u, v = cu + math.cos(a) * rad, cv + math.sin(a) * rad
            put(site, "Chair", u, v, face=(cu - u, cv - v))

    camp_no = 0
    for site in out["sites"]:
        f, r = frame(site)
        site["_f"], site["_r"] = f, r
        kind = site["kind"]
        if kind == "camp":
            bunk = prng.choice(("Bunker_A", "Bunker_B", "Bunker_C"))
            put(site, bunk, 0.0, -12.0 if bunk != "Bunker_C" else -13.0, face=(0, 1), collider="mesh")
            if camp_no % 2 == 0:
                put(site, "Bunker_B" if bunk != "Bunker_B" else "Bunker_A", -12.5, -3.0, face=(1, 0), collider="mesh")
            for k, v in enumerate((-6.0, 1.5)):
                put(site, "Tent", 11.5, v, face=(-1, 0), collider="mesh")
            put(site, "Campfire", 0.0, 2.0, face=(0, 1), collider=None, role="fire")
            chairs_round(site, 0.0, 2.0, 2.3, 3, prng.uniform(0, 6.28))
            put(site, "Table", -6.0, 6.0, face=(0, 1))
            put(site, "Chair", -6.0, 7.0, face=(0, -1))
            put(site, "Chair", -6.0, 5.0, face=(0, 1))
            for k in range(2):
                c = put(site, "SupplyCrate", 4.2 + k * 1.2, -9.0, face=(0, 1), turn=prng.uniform(-6, 6))
                c["role"] = "crate"
                c["loot"] = {"ammo": 21, "heal": 30} if k == 0 else {"ammo": 30}
            barrels(site, -4.6, -8.8, 2, 2)
            barrels(site, 7.5, 9.5, 1, 1, lid=False)
            # a checkpoint where the road comes in: two barriers with a gap for trucks
            for u in (-4.2, 4.2):
                put(site, "Barrier", u, 17.5, face=(0, 1), turn=prng.uniform(-4, 4))
            put(site, "Barrier", 8.5, 15.5, face=(0, 1), turn=25.0)
            if camp_no == 0:
                put(site, "M3S", -10.0, 10.0, face=(0, 1), collider="vehicle", role="vehicle")
            guards = [(1.5, 4.0, "MP5"), (-2.5, 0.5, "pistol"), (6.0, -2.0, "pistol"), (0.0, 15.0, "MP5"),
                      (-9.0, 2.0, "pistol"), (3.0, -6.0, "MP5")]
            for u, v, w in guards[:4 + camp_no % 3]:
                post(site, u, v, "soldier", w)
            camp_no += 1
        elif kind == "radio":
            put(site, "soviet_radio_station", 0.0, -3.0, face=(0, 1), collider="mesh", role="radio")
            for k, (u, v) in enumerate(((-3.0, 2.0), (3.4, 2.4))):
                it = put(site, "Barrel_Explosive_Red", u, v, face=(1, 0), turn=prng.uniform(0, 360))
                it["role"] = "explosive"
                it["objective"] = "radio"
            put(site, "Barrel_Yellow_Damaged", -3.6, 2.9, face=(1, 0))
            c = put(site, "SupplyCrate", 5.5, -4.0, face=(-1, 0))
            c["role"], c["loot"] = "crate", {"ammo": 30, "heal": 40}
            put(site, "Barrier", -2.5, 9.0, face=(0, 1))
            put(site, "Barrier", 2.5, 9.0, face=(0, 1))
            for u, v, w in ((-5.0, 4.0, "MP5"), (5.0, 5.0, "pistol"), (0.0, 7.5, "pistol")):
                post(site, u, v, "soldier", w)
        elif kind == "village":
            put(site, "Campfire", 0.0, 0.0, face=(0, 1), collider=None, role="fire")
            n = 4 + prng.randrange(2)
            for k in range(n):
                a = k * (2 * math.pi / n) + prng.uniform(-0.2, 0.2)
                u, v = math.cos(a) * 8.0, math.sin(a) * 8.0
                put(site, "Tent", u, v, face=(-u, -v), collider="mesh")
            put(site, "Barrel_Yellow_Damaged", 3.5, -5.2, face=(1, 0), turn=prng.uniform(0, 360))
            put(site, "Barrel_Yellow_Damaged", 4.1, -4.6, face=(1, 0), turn=prng.uniform(0, 360))
            c = put(site, "SupplyCrate", -4.0, 5.5, face=(1, -1))
            c["role"], c["loot"] = "crate", {"heal": 50}
            for k in range(4 + prng.randrange(3)):
                a = prng.uniform(0, 2 * math.pi)
                rr = prng.uniform(2.6, 5.5)
                post(site, math.cos(a) * rr, math.sin(a) * rr, "native", None,
                     face=(-math.cos(a), -math.sin(a)))
        elif kind == "crash":
            put(site, "Li2", 0.0, 0.0, face=(1, 0.35), collider="mesh", y=-1.05, pitch=-5.0, roll=11.0, role="plane")
            for k, (u, v) in enumerate(((7.0, 6.0), (-8.0, -4.0), (3.0, -9.0))):
                c = put(site, "SupplyCrate", u, v, face=(prng.uniform(-1, 1), 1), turn=prng.uniform(-30, 30),
                        roll=prng.uniform(-6, 6))
                c["role"] = "crate"
                c["loot"] = {"ammo": 30} if k else {"ammo": 30, "heal": 40}
            put(site, "Barrel_Yellow_Damaged", -5.0, 7.0, face=(1, 0), roll=88.0)
            it = put(site, "Barrel_Explosive_Red", 9.0, -2.0, face=(1, 0))
            it["role"] = "explosive"
            out["loot"].append({"x": float(site["x"] + r[0] * 4.0 + f[0] * 4.5),
                                "z": float(site["z"] + r[1] * 4.0 + f[1] * 4.5), "weapon": "MP5", "ammo": 30,
                                "site": site["index"]})

    # ---- wrecks along the roads (cover, and something to find)
    if len(road_pts) > 20:
        cand = list(range(10, len(road_pts) - 10, 7))
        prng.shuffle(cand)
        placed = 0
        for k in cand:
            x, z = road_pts[k]
            if any(math.hypot(x - s["x"], z - s["z"]) < 60 for s in out["sites"]):
                continue
            if math.hypot(x - sx, z - sz) < 70:
                continue
            if any(math.hypot(x - it["x"], z - it["z"]) < 80 for it in out["items"]
                   if it["model"] in ("M3S_Wreck", "pickup_wreck")):
                continue
            a = road_pts[min(k + 2, len(road_pts) - 1)] - road_pts[max(k - 2, 0)]
            a = a / max(np.linalg.norm(a), 1e-9)
            side = np.array([-a[1], a[0]]) * (6.5 if prng.random() < 0.5 else -6.5)
            model = "M3S_Wreck" if placed % 2 == 0 else "pickup_wreck"
            wx, wz = x + side[0], z + side[1]
            out["items"].append({"model": model, "x": float(wx), "z": float(wz),
                                 "yaw": yaw_facing(model, a[0], a[1]) + prng.uniform(-25, 25),
                                 "collider": "mesh", "tilt": True, "site": -1, "y_off": -0.12})
            out["items"].append({"model": "Barrel_Yellow_Damaged", "x": float(wx + a[0] * 3.5),
                                 "z": float(wz + a[1] * 3.5), "yaw": prng.uniform(0, 360), "roll": 86.0,
                                 "collider": "box", "site": -1})
            placed += 1
            if placed >= 3:
                break

    # ---- the landing: the player starts on the beach, the pickup waits by the road
    fx, fz = -sx, -sz
    if len(road_pts):
        d = np.hypot(road_pts[:, 0] - sx, road_pts[:, 1] - sz)
        k = int(np.argmin(d))
        k2 = min(k + 6, len(road_pts) - 1) if k < len(road_pts) - 6 else max(k - 6, 0)
        fx, fz = road_pts[k2, 0] - road_pts[k, 0], road_pts[k2, 1] - road_pts[k, 1]
    fl = max(math.hypot(fx, fz), 1e-9)
    fx, fz = fx / fl, fz / fl
    rx, rz = -fz, fx
    out["start"] = {"x": float(sx), "z": float(sz), "yaw": math.degrees(math.atan2(-fx, -fz))}
    out["items"].append({"model": "pickup_healthy", "x": float(sx + rx * 4.0 + fx * 3.0),
                         "z": float(sz + rz * 4.0 + fz * 3.0), "yaw": yaw_facing("pickup_healthy", fx, fz),
                         "collider": "vehicle", "role": "vehicle", "site": -1})
    out["items"].append({"model": "SupplyCrate", "x": float(sx - rx * 2.5), "z": float(sz - rz * 2.5),
                         "yaw": yaw_facing("SupplyCrate", rx, rz), "collider": "box", "site": -1,
                         "role": "crate", "loot": {"ammo": 14}})
    out["pads"].append((float(sx + rx * 4.0 + fx * 3.0), float(sz + rz * 4.0 + fz * 3.0), 4.0, None))
    for s in out["sites"]:
        s.pop("_f", None)
        s.pop("_r", None)
    return out


def apply_pads(isl, layout):
    """Flatten every site to its median height (soft edges), paint it as
    trodden dirt and clear trees, rocks and grass off it.  In place."""
    g = _Grid(isl)
    h = np.asarray(isl.heights, np.float64).copy()
    gi, gj = np.mgrid[0:g.N, 0:g.N]
    gx = gj * g.cell - g.ext / 2
    gz = gi * g.cell - g.ext / 2
    sp = isl.splat
    S = sp.shape[0]
    si, sj = np.mgrid[0:S, 0:S]
    sx = sj / (S - 1) * g.ext - g.ext / 2
    sz = si / (S - 1) * g.ext - g.ext / 2
    sp = sp.astype(np.float32)
    for x, z, r, target in layout["pads"]:
        d = np.hypot(gx - x, gz - z)
        if target is not None:
            w = np.clip((r * 1.45 - d) / (r * 0.45), 0.0, 1.0)
            w = w * w * (3 - 2 * w)
            h = h * (1 - w) + target * w
        ds = np.hypot(sx - x, sz - z)
        wd = np.clip((r * 1.05 - ds) / (r * 0.35), 0.0, 1.0)[..., None]
        dirt = np.zeros(4, np.float32)
        dirt[2] = 255.0                               # sand, grass, dirt, rock
        mix = dirt * 0.8
        mix[1] = 51.0
        sp = sp * (1 - wd * 0.85) + mix * wd * 0.85
    isl.splat = np.clip(sp + 0.5, 0, 255).astype(np.uint8)
    isl.heights = h.astype(np.float32)
    # vegetation off the pads (trees a little further out: their crowns)
    pads = np.array([(x, z, r) for x, z, r, _t in layout["pads"]] +
                    [(it["x"], it["z"], 5.5) for it in layout["items"] if it.get("site", 0) == -1], np.float64)
    if len(pads):
        for sp_name, inst in list(isl.instances.items()):
            if inst is None or not len(inst):
                continue
            grow = 1.35 if sp_name in TREES else 1.05
            d = np.hypot(inst[:, 0][:, None] - pads[:, 0][None, :], inst[:, 2][:, None] - pads[:, 1][None, :])
            keep = (d > pads[:, 2][None, :] * grow + (3.0 if sp_name in TREES else 0.0)).all(axis=1)
            isl.instances[sp_name] = inst[keep]

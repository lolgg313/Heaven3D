# @heaven3d-part world/bk_building.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# BUILDING MAKER - buildings painted on a grid, baked into a few meshes
#
# A building is ONE scene object.  Its mesh_params hold a small grid:
#   cells   "x,y,z" -> finish index     (2 m x 3 m x 2 m cells)
#   edges   "x,y,z,N|E" -> kind         (overrides of the automatic walls:
#                                        none / wall / door / window / arch /
#                                        ruin / half / sandbags)
#   stairs  "x,y,z" -> direction 0..3   (a flight over two cells)
#   open    ["x,y,z", ...]              (cells without ceiling / roof)
# Walls appear by themselves between a room and the outside, and between
# rooms with a different finish (the FPS Creator segment rule).  Everything
# is baked per storey into one mesh per material (thick walls with different
# faces inside and outside, window frames + glass + shutters, doors, stairs,
# string courses, plinth, roof) plus one low-poly collision mesh, so a whole
# building costs a handful of draw calls and one static collider.
# #############################################################################

_BK_C, _BK_H, _BK_SLAB = 2.0, 3.0, 0.20
_BK_WH = _BK_H - _BK_SLAB                       # clear wall height
_BK_MESH_TYPE = "bk_building"
_BK_KEEP = object()
_BK_WALLISH = ("wall", "door", "window", "arch")

# interior finishes: wall + floor (+ ceiling) materials of a room
BK_FINISHES = {
    "wallpaper": {"label": "Wallpaper, plank floor", "wall": "wallpaper", "floor": "wood_floor"},
    "wallpaper_red": {"label": "Red wallpaper, plank floor", "wall": "wallpaper_red", "floor": "wood_floor"},
    "plaster": {"label": "Plaster, plank floor", "wall": "plaster_int", "floor": "wood_floor"},
    "hall": {"label": "Plaster, checker tiles", "wall": "plaster_int", "floor": "checker"},
    "green": {"label": "Green paint, concrete floor", "wall": "plaster_green", "floor": "concrete_floor"},
    "brick": {"label": "Bare brick, concrete floor", "wall": "brick_yellow", "floor": "concrete_floor"},
    "concrete": {"label": "Concrete", "wall": "concrete", "floor": "concrete_floor", "ceil": "concrete"},
    "cellar": {"label": "Stone, cobbles", "wall": "stone", "floor": "cobble", "ceil": "plaster_int"},
    "yard": {"label": "Yard: cobbles (outdoor)", "wall": None, "floor": "cobble", "outdoor": True},
    "yard_concrete": {"label": "Yard: concrete (outdoor)", "wall": None, "floor": "concrete_floor", "outdoor": True},
}

BK_STYLES = {
    "town": {"label": "Town house - red brick", "ext": "brick_red", "trim": "stone", "ceil": "plaster_int",
             "roof": "roof_tiles", "roof_type": "gable", "pitch": 42.0, "frame": "wood_white",
             "door": "wood_green", "shutter": "wood_green", "stairs": "wood_light", "t": 0.30,
             "window": "sash", "beams": True, "chimney": True, "finishes": ["wallpaper", "plaster", "hall"]},
    "village": {"label": "Farmhouse - whitewash", "ext": "plaster_white", "trim": "stone", "ceil": "plaster_int",
                "roof": "roof_tiles", "roof_type": "gable", "pitch": 45.0, "frame": "wood_beam",
                "door": "wood_red", "shutter": "wood_blue", "stairs": "wood_light", "t": 0.34,
                "window": "sash", "beams": True, "chimney": True, "finishes": ["plaster", "wallpaper_red", "cellar"]},
    "manor": {"label": "Manor - ochre render, slate", "ext": "plaster_ochre", "trim": "stone",
              "ceil": "plaster_int", "roof": "roof_slate", "roof_type": "hip", "pitch": 38.0,
              "frame": "wood_white", "door": "wood_trim", "shutter": "wood_white", "stairs": "wood_light",
              "t": 0.32, "window": "tall", "beams": False, "chimney": True,
              "finishes": ["wallpaper_red", "hall", "wallpaper"]},
    "rose": {"label": "Row house - rose render", "ext": "plaster_rose", "trim": "stone", "ceil": "plaster_int",
             "roof": "roof_tiles", "roof_type": "gable", "pitch": 40.0, "frame": "wood_white",
             "door": "wood_blue", "shutter": "wood_red", "stairs": "wood_light", "t": 0.30,
             "window": "sash", "beams": True, "chimney": True, "finishes": ["wallpaper", "plaster", "hall"]},
    "factory": {"label": "Factory - sooty brick, iron roof", "ext": "brick_dark", "trim": "concrete",
                "ceil": "concrete", "roof": "corrugated", "roof_type": "gable", "pitch": 22.0, "frame": "steel",
                "door": "steel", "shutter": None, "stairs": "concrete_floor", "t": 0.34, "window": "industrial",
                "beams": False, "chimney": False, "finishes": ["brick", "green", "concrete"]},
    "bunker": {"label": "Bunker - board-formed concrete", "ext": "concrete", "trim": "concrete",
               "ceil": "concrete", "roof": "concrete_floor", "roof_type": "flat", "pitch": 0.0,
               "frame": "steel", "door": "steel", "shutter": None, "stairs": "concrete_floor", "t": 0.50,
               "window": "slit", "beams": False, "chimney": False, "finishes": ["concrete", "green"]},
    "stone": {"label": "Stone hall - ashlar, slate", "ext": "stone", "trim": "stone", "ceil": "plaster_int",
              "roof": "roof_slate", "roof_type": "gable", "pitch": 52.0, "frame": "wood_beam",
              "door": "wood_trim", "shutter": None, "stairs": "wood_light", "t": 0.40, "window": "tall",
              "beams": True, "chimney": False, "finishes": ["cellar", "plaster", "hall"]},
}
BK_STYLE_ORDER = ("town", "village", "rose", "manor", "stone", "factory", "bunker")

# window shapes: half width, sill, head, glazing bars (vertical, horizontal)
_BK_WINDOWS = {"sash": (0.50, 0.90, 2.20, 1, 2), "tall": (0.42, 0.70, 2.35, 1, 3),
               "industrial": (0.78, 1.00, 2.50, 3, 3), "slit": (0.60, 1.25, 1.55, 0, 0)}


def _bk_hash(*parts):
    """Deterministic 0..1 from integers / strings."""
    return (_fp_fnv1a("|".join(str(p) for p in parts)) % 100003) / 100003.0


def _bk_key(k):
    return ",".join(str(v) for v in k)


class BKData:
    """A building's grid (tuple keys in memory, "x,y,z" strings on disk)."""

    def __init__(self, params=None):
        p = params or {}
        self.style = p.get("style") if p.get("style") in BK_STYLES else "town"
        self.seed = int(p.get("seed", 1))
        self.roof = dict(p.get("roof") or {})
        self.finishes = [f for f in (p.get("finishes") or []) if f in BK_FINISHES] \
            or list(BK_STYLES[self.style]["finishes"])
        self.cells, self.edges, self.stairs, self.open = {}, {}, {}, set()
        try:
            for k, v in (p.get("cells") or {}).items():
                self.cells[tuple(int(t) for t in k.split(","))] = int(v) % max(len(self.finishes), 1)
            for k, v in (p.get("edges") or {}).items():
                a = k.split(",")
                if a[3] in ("N", "E"):
                    self.edges[(int(a[0]), int(a[1]), int(a[2]), a[3])] = str(v)
            for k, v in (p.get("stairs") or {}).items():
                self.stairs[tuple(int(t) for t in k.split(","))] = int(v) % 4
            for k in (p.get("open") or []):
                self.open.add(tuple(int(t) for t in k.split(",")))
        except Exception as ex:
            Log.warn(f"[Building Maker] damaged building data ignored in part: {ex}")

    def to_params(self):
        return {"bk": 1, "style": self.style, "seed": self.seed, "roof": dict(self.roof),
                "finishes": list(self.finishes),
                "cells": {_bk_key(k): v for k, v in sorted(self.cells.items())},
                "edges": {_bk_key(k): v for k, v in sorted(self.edges.items())},
                "stairs": {_bk_key(k): v for k, v in sorted(self.stairs.items())},
                "open": [_bk_key(k) for k in sorted(self.open)]}

    # ---- queries ----
    def st(self):
        return BK_STYLES[self.style]

    def finish(self, c):
        i = self.cells.get(c)
        return None if i is None else BK_FINISHES[self.finishes[i % len(self.finishes)]]

    def indoor(self, c):
        f = self.finish(c)
        return f is not None and not f.get("outdoor")

    def levels(self):
        ys = [k[1] for k in self.cells]
        return (min(ys), max(ys)) if ys else (0, 0)

    def finish_index(self, name):
        if name not in BK_FINISHES:
            name = self.finishes[0]
        if name not in self.finishes:
            self.finishes.append(name)
        return self.finishes.index(name)

    @staticmethod
    def edge_cells(e):
        x, y, z, o = e
        return (x, y, z), ((x, y, z + 1) if o == "N" else (x + 1, y, z))

    def edge_kind(self, e):
        """The wall on an edge: an override, or the automatic rule."""
        k = self.edges.get(e)
        if k is not None:
            return None if k == "none" else k
        a, b = self.edge_cells(e)
        ia, ib = self.indoor(a), self.indoor(b)
        if ia != ib:
            return "wall"
        if ia and ib and self.cells[a] != self.cells[b]:
            return "wall"
        return None

    def stair_cells(self):
        """{cell: (start cell, direction)} for both cells of every flight."""
        out = {}
        for (x, y, z), d in self.stairs.items():
            dx, dz = ((1, 0), (0, 1), (-1, 0), (0, -1))[d % 4]
            out[(x, y, z)] = ((x, y, z), d)
            out[(x + dx, y, z + dz)] = ((x, y, z), d)
        return out

    def roof_type(self):
        return self.roof.get("type") or self.st()["roof_type"]

    def roof_pitch(self):
        try:
            return float(np.clip(float(self.roof.get("pitch", self.st()["pitch"])), 5.0, 65.0))
        except Exception:
            return self.st()["pitch"]


# ---------------------------------------------------------------------------
#  geometry accumulation (per material) with world-planar "box" UVs
# ---------------------------------------------------------------------------
class _BKGeo:
    def __init__(self):
        self.m = {}                    # material -> [positions, normals, uvs] flat lists
        self.col = []                  # collision boxes (x0, y0, z0, x1, y1, z1)
        self.col_tris = []             # extra collision triangles (9 floats each)

    def quad(self, mat, p0, p1, p2, p3, uv=None, out=None, up=False):
        """A quad (corners in order).  ``out``: a point the face must look
        away from; ``up``: the face must look upwards."""
        ax, ay, az = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        bx, by, bz = p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2]
        nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-12:
            return
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        flip = False
        if out is not None:
            flip = (nx * (p0[0] - out[0]) + ny * (p0[1] - out[1]) + nz * (p0[2] - out[2])) < 0
        elif up:
            flip = ny < 0
        if flip:
            p1, p3 = p3, p1
            nx, ny, nz = -nx, -ny, -nz
            if uv is not None:
                uv = (uv[0], uv[3], uv[2], uv[1])
        tw, th = _BK_TILE.get(mat, (2.0, 2.0))
        if uv is None:
            anx, any_, anz = abs(nx), abs(ny), abs(nz)
            if any_ >= anx and any_ >= anz:
                uv = [(p[0], p[2] if ny > 0 else -p[2]) for p in (p0, p1, p2, p3)]
            elif anx >= anz:
                uv = [(-p[2] if nx > 0 else p[2], p[1]) for p in (p0, p1, p2, p3)]
            else:
                uv = [(p[0] if nz > 0 else -p[0], p[1]) for p in (p0, p1, p2, p3)]
        g = self.m.get(mat)
        if g is None:
            g = self.m[mat] = [[], [], []]
        P, N, U = g
        for p, t in ((p0, uv[0]), (p1, uv[1]), (p2, uv[2]), (p3, uv[3])):
            P.extend(p)
            N.extend((nx, ny, nz))
            U.extend((t[0] / tw, t[1] / th))

    def tri(self, mat, p0, p1, p2, uv, out=None, up=False):
        self.quad(mat, p0, p1, p2, p2, (uv[0], uv[1], uv[2], uv[2]), out, up)

    def box(self, lo, hi, mats, collide=False):
        """Axis-aligned box.  mats: a material for every face, or a dict
        {"x-", "x+", "y-", "y+", "z-", "z+"} (missing faces are skipped)."""
        x0, y0, z0 = min(lo[0], hi[0]), min(lo[1], hi[1]), min(lo[2], hi[2])
        x1, y1, z1 = max(lo[0], hi[0]), max(lo[1], hi[1]), max(lo[2], hi[2])
        if x1 - x0 < 1e-6 or y1 - y0 < 1e-6 or z1 - z0 < 1e-6:
            return
        if collide:
            self.col.append((x0, y0, z0, x1, y1, z1))
        if mats is None:
            return
        g = (lambda k: mats) if isinstance(mats, str) else mats.get
        c = ((x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5)
        m = g("x-")
        if m:
            self.quad(m, (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0), out=c)
        m = g("x+")
        if m:
            self.quad(m, (x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), out=c)
        m = g("z-")
        if m:
            self.quad(m, (x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), out=c)
        m = g("z+")
        if m:
            self.quad(m, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), out=c)
        m = g("y-")
        if m:
            self.quad(m, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), out=c)
        m = g("y+")
        if m:
            self.quad(m, (x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0), out=c)

    def arrays(self):
        """{material: (positions, normals, uvs, indices)}"""
        out = {}
        for mat, (P, N, U) in self.m.items():
            n = len(P) // 3
            if not n:
                continue
            q = np.arange(0, n, 4, dtype=np.uint32)
            idx = np.stack([q, q + 1, q + 2, q, q + 2, q + 3], 1).ravel()
            out[mat] = (np.asarray(P, np.float32).reshape(-1, 3), np.asarray(N, np.float32).reshape(-1, 3),
                        np.asarray(U, np.float32).reshape(-1, 2), idx)
        return out

    def collision(self):
        """(positions (n, 3), indices) of the collision boxes + triangles."""
        P, I = [], []
        if self.col:
            b = np.asarray(self.col, np.float32)
            x0, y0, z0, x1, y1, z1 = [b[:, i] for i in range(6)]
            corners = np.stack([np.stack([x0, y0, z0], 1), np.stack([x1, y0, z0], 1), np.stack([x1, y0, z1], 1),
                                np.stack([x0, y0, z1], 1), np.stack([x0, y1, z0], 1), np.stack([x1, y1, z0], 1),
                                np.stack([x1, y1, z1], 1), np.stack([x0, y1, z1], 1)], 1)       # (n, 8, 3)
            tri = np.array([0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1, 1, 5, 6, 1, 6, 2,
                            2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0], np.uint32)
            P.append(corners.reshape(-1, 3))
            I.append((tri[None, :] + (np.arange(len(b), dtype=np.uint32) * 8)[:, None]).ravel())
        if self.col_tris:
            t = np.asarray(self.col_tris, np.float32).reshape(-1, 3)
            base = sum(len(p) for p in P)
            P.append(t)
            I.append(np.arange(len(t), dtype=np.uint32) + base)
        if not P:
            return np.zeros((0, 3), np.float32), np.zeros(0, np.uint32)
        return np.concatenate(P), np.concatenate(I)


class _BKEdge:
    """Local frame of a wall edge: a along the wall (0.._BK_C), n across it
    (from cell a towards cell b), y up from the storey's floor."""

    def __init__(self, e):
        x, y, z, o = e
        self.o = o
        self.y0 = y * _BK_H
        if o == "N":
            self.ox, self.oz = x * _BK_C, (z + 1) * _BK_C
        else:
            self.ox, self.oz = (x + 1) * _BK_C, z * _BK_C
        self.face = ({"a-": "x-", "a+": "x+", "n-": "z-", "n+": "z+"} if o == "N" else
                     {"a-": "z-", "a+": "z+", "n-": "x-", "n+": "x+"})

    def pt(self, a, y, n):
        if self.o == "N":
            return (self.ox + a, self.y0 + y, self.oz + n)
        return (self.ox + n, self.y0 + y, self.oz + a)

    def box(self, geo, a0, a1, y0, y1, n0, n1, mats, collide=False):
        if isinstance(mats, dict):
            mats = {(self.face.get(k, k)): v for k, v in mats.items()}
        geo.box(self.pt(a0, y0, n0), self.pt(a1, y1, n1), mats, collide)


# ---------------------------------------------------------------------------
#  baking one storey
# ---------------------------------------------------------------------------
def _bk_side_mat(d, cell):
    f = d.finish(cell)
    if f is not None and not f.get("outdoor"):
        return f["wall"]
    return d.st()["ext"]


def _bk_wall_piece(geo, E, a0, a1, y0, y1, ma, mb, t, top=None, ends=(None, None)):
    mats = {"n-": ma, "n+": mb}
    if top:
        mats["y+"] = top
    if ends[0]:
        mats["a-"] = ends[0]
    if ends[1]:
        mats["a+"] = ends[1]
    E.box(geo, a0, a1, y0, y1, -t / 2, t / 2, mats, collide=True)


def _bk_reveals(geo, E, a0, a1, y0, y1, t, mat, sill=True, n0=None, n1=None):
    """The inside faces of an opening through the wall (between n0 and n1)."""
    lo = -t / 2 if n0 is None else min(n0, n1)
    hi = t / 2 if n1 is None else max(n0, n1)
    if hi - lo < 1e-4:
        return
    c = E.pt((a0 + a1) / 2, (y0 + y1) / 2, (lo + hi) / 2)

    def q(p0, p1, p2, p3):
        w = E.pt(*p0)
        geo.quad(mat, w, E.pt(*p1), E.pt(*p2), E.pt(*p3),
                 out=(2 * w[0] - c[0], 2 * w[1] - c[1], 2 * w[2] - c[2]))
    q((a0, y0, lo), (a0, y0, hi), (a0, y1, hi), (a0, y1, lo))
    q((a1, y0, lo), (a1, y0, hi), (a1, y1, hi), (a1, y1, lo))
    q((a0, y1, lo), (a1, y1, lo), (a1, y1, hi), (a0, y1, hi))
    if sill:
        q((a0, y0, lo), (a1, y0, lo), (a1, y0, hi), (a0, y0, hi))


def _bk_rot_box(geo, E, mat, hinge_a, hinge_n, width, thick, y0, y1, ang, tile_uv=True):
    """A door leaf: a box hinged at (hinge_a, hinge_n), swung by ``ang``."""
    ca, sa = math.cos(ang), math.sin(ang)

    def P(u, w, y):                       # u along the leaf, w across its thickness
        return E.pt(hinge_a + u * ca - w * sa, y, hinge_n + u * sa + w * ca)
    c = P(width / 2, thick / 2, (y0 + y1) / 2)
    for (u0, w0, u1, w1) in ((0, 0, width, 0), (0, thick, width, thick)):          # the two big faces
        geo.quad(mat, P(u0, w0, y0), P(u1, w1, y0), P(u1, w1, y1), P(u0, w0, y1),
                 uv=[(0, y0), (width, y0), (width, y1), (0, y1)], out=c)
    for u in (0, width):                                                            # the edges
        geo.quad(mat, P(u, 0, y0), P(u, thick, y0), P(u, thick, y1), P(u, 0, y1),
                 uv=[(0, y0), (thick, y0), (thick, y1), (0, y1)], out=c)
    geo.quad(mat, P(0, 0, y1), P(width, 0, y1), P(width, thick, y1), P(0, thick, y1),
             uv=[(0, 0), (width, 0), (width, thick), (0, thick)], out=c)


def _bk_window(geo, d, E, e, s, ma, mb, t, exterior):
    st = d.st()
    hw, y0, y1, bars_v, bars_h = _BK_WINDOWS.get(st["window"], _BK_WINDOWS["sash"])
    c = _BK_C / 2
    a0, a1 = c - hw, c + hw
    lo, hi = t / 2, _BK_C - t / 2
    _bk_wall_piece(geo, E, lo, a0, 0, _BK_H, ma, mb, t)
    _bk_wall_piece(geo, E, a1, hi, 0, _BK_H, ma, mb, t)
    _bk_wall_piece(geo, E, a0, a1, 0, y0, ma, mb, t)
    _bk_wall_piece(geo, E, a0, a1, y1, _BK_H, ma, mb, t)
    slit = st["window"] == "slit"
    nf = s * (t / 2 - 0.10) if (exterior and not slit) else 0.0     # the frame sits back from the outer face
    if exterior and not slit:
        inner = ma if s > 0 else mb
        _bk_reveals(geo, E, a0, a1, y0, y1, t, st["ext"], n0=nf, n1=s * t / 2)
        _bk_reveals(geo, E, a0, a1, y0, y1, t, inner, n0=-s * t / 2, n1=nf)
    else:
        _bk_reveals(geo, E, a0, a1, y0, y1, t, st["ext"] if exterior else ma)
    if slit:
        return
    fm = st["frame"]
    fd, fw = 0.035, 0.05
    E.box(geo, a0, a0 + fw, y0, y1, nf - fd, nf + fd, fm)
    E.box(geo, a1 - fw, a1, y0, y1, nf - fd, nf + fd, fm)
    E.box(geo, a0 + fw, a1 - fw, y0, y0 + fw, nf - fd, nf + fd, fm)
    E.box(geo, a0 + fw, a1 - fw, y1 - fw, y1, nf - fd, nf + fd, fm)
    bw = 0.018 if st["window"] == "industrial" else 0.024
    for i in range(bars_v):
        ac = a0 + (a1 - a0) * (i + 1) / (bars_v + 1)
        E.box(geo, ac - bw, ac + bw, y0 + fw, y1 - fw, nf - fd * 0.7, nf + fd * 0.7, fm)
    for i in range(bars_h):
        yc = y0 + (y1 - y0) * (i + 1) / (bars_h + 1)
        E.box(geo, a0 + fw, a1 - fw, yc - bw, yc + bw, nf - fd * 0.7, nf + fd * 0.7, fm)
    g0, g1 = E.pt(a0 + fw, y0 + fw, nf), E.pt(a1 - fw, y1 - fw, nf)
    geo.quad("glass", g0, E.pt(a1 - fw, y0 + fw, nf), g1, E.pt(a0 + fw, y1 - fw, nf),
             uv=[(0, 0), (1, 0), (1, 1), (0, 1)])
    E.box(geo, a0, a1, y0, y1, nf - 0.02, nf + 0.02, None, collide=True)
    if not exterior:
        return
    tr = st["trim"]
    E.box(geo, a0 - 0.08, a1 + 0.08, y0 - 0.08, y0, s * (t / 2 - 0.10), s * (t / 2 + 0.07), tr)      # sill
    E.box(geo, a0 - 0.12, a1 + 0.12, y1, y1 + 0.20, s * (t / 2 - 0.005), s * (t / 2 + 0.03), tr)     # lintel
    E.box(geo, a0 - 0.04, a1 + 0.04, y0 - 0.035, y0, -s * (t / 2 + 0.05), s * (t / 2 - 0.13), "wood_beam")
    if st.get("shutter") and _bk_hash("sh", d.seed, *e[:3]) > 0.12:
        sw = min(hw, a0 - 0.03)
        if sw > 0.15:
            for b0, b1 in ((a0 - sw, a0 - 0.01), (a1 + 0.01, a1 + sw)):
                E.box(geo, b0, b1, y0 - 0.02, y1 + 0.02, s * (t / 2 + 0.012), s * (t / 2 + 0.05), st["shutter"])


def _bk_door(geo, d, E, e, s, ma, mb, t, exterior, wide=False):
    st = d.st()
    c = _BK_C / 2
    hw, top = (0.78, 2.30) if wide else (0.50, 2.10)
    a0, a1 = c - hw, c + hw
    lo, hi = t / 2, _BK_C - t / 2
    _bk_wall_piece(geo, E, lo, a0, 0, _BK_H, ma, mb, t)
    _bk_wall_piece(geo, E, a1, hi, 0, _BK_H, ma, mb, t)
    _bk_wall_piece(geo, E, a0, a1, top, _BK_H, ma, mb, t)
    if wide:                                                # an open archway with a timber lintel
        _bk_reveals(geo, E, a0, a1, 0, top, t, st["ext"] if exterior else ma, sill=False)
        E.box(geo, a0 - 0.12, a1 + 0.12, top - 0.02, top + 0.18, -t / 2 - 0.025, t / 2 + 0.025, "wood_beam")
        return
    fm = "wood_beam" if st["door"].startswith("wood") else st["door"]
    lw = 0.05                                               # the door lining covers the reveals
    E.box(geo, a0, a0 + lw, 0, top, -t / 2 - 0.02, t / 2 + 0.02, fm)
    E.box(geo, a1 - lw, a1, 0, top, -t / 2 - 0.02, t / 2 + 0.02, fm)
    E.box(geo, a0, a1, top - lw, top, -t / 2 - 0.02, t / 2 + 0.02, fm)
    E.box(geo, a0, a1, 0.0, 0.025, -t / 2 - 0.03, t / 2 + 0.03, st["trim"])          # threshold
    # the leaf stands open into the room (the indoor side), so play is never blocked
    inside = -s if exterior else (1.0 if _bk_hash("dr", d.seed, *e[:3]) > 0.5 else -1.0)
    ang = math.radians(98.0 + 30.0 * _bk_hash("da", d.seed, *e[:3])) * inside
    _bk_rot_box(geo, E, st["door"], a0 + lw + 0.01, inside * (t / 2 - 0.02), a1 - a0 - 2 * lw - 0.02, 0.045,
                0.03, top - lw - 0.01, ang)
    if exterior:
        E.box(geo, a0 - 0.12, a1 + 0.12, top, top + 0.20, s * (t / 2 - 0.005), s * (t / 2 + 0.03), st["trim"])


def _bk_ruin_height(d, vx, y, vz):
    return 0.55 + 1.9 * _bk_hash("rh", d.seed, vx, y, vz)


def _bk_ruin(geo, d, E, e, ma, mb, t):
    """A shelled wall: brick-sized steps down from both ends."""
    x, y, z, o = e
    v0 = (x, z + 1) if o == "N" else (x + 1, z)
    v1 = (x + 1, z + 1) if o == "N" else (x + 1, z + 1)
    h0, h1 = _bk_ruin_height(d, v0[0], y, v0[1]), _bk_ruin_height(d, v1[0], y, v1[1])
    lo, hi = t / 2, _BK_C - t / 2
    n = 8
    hs = []
    for i in range(n):
        f = (i + 0.5) / n
        h = h0 + (h1 - h0) * f + (_bk_hash("rs", d.seed, x, y, z, o, i) - 0.5) * 0.9 - 0.5 * math.sin(f * math.pi)
        hs.append(float(np.clip(round(h / 0.075) * 0.075, 0.15, _BK_WH)))
    for i in range(n):
        b0, b1 = lo + (hi - lo) * i / n, lo + (hi - lo) * (i + 1) / n
        prev = hs[i - 1] if i else h0
        nxt = hs[i + 1] if i < n - 1 else h1
        E.box(geo, b0, b1, 0, hs[i], -t / 2, t / 2,
              {"n-": ma, "n+": mb, "y+": "rubble", "a-": "rubble" if prev < hs[i] else None,
               "a+": "rubble" if nxt < hs[i] else None}, collide=True)
    # a heap of fallen masonry at the foot of the wall
    side = 1.0 if _bk_hash("rb", d.seed, x, y, z, o) > 0.5 else -1.0
    ca = lo + (hi - lo) * (0.3 + 0.4 * _bk_hash("rc", d.seed, x, y, z, o))
    top = E.pt(ca, 0.30 + 0.25 * _bk_hash("rt", d.seed, x, y, z), side * (t / 2 + 0.25))
    ring = []
    for k in range(9):
        an = k / 8.0 * math.pi
        r = 0.55 + 0.45 * _bk_hash("rr", d.seed, x, z, o, k % 8)
        ring.append(E.pt(ca + math.cos(an) * r * 1.3, 0.0, side * (t / 2 + math.sin(an) * r)))
    for k in range(8):
        geo.tri("rubble", top, ring[k], ring[k + 1],
                [(top[0], top[2]), (ring[k][0], ring[k][2]), (ring[k + 1][0], ring[k + 1][2])], up=True)


def _bk_sandbags(geo, E, t):
    """Four courses of bags, broken-bond, 0.9 m high."""
    bw, bh, bd = 0.60, 0.22, 0.34
    for row in range(4):
        y0 = row * bh
        off = (bw / 2) if row % 2 else 0.0
        a = -off + 0.02
        while a < _BK_C - 0.05:
            a0, a1 = max(a, 0.0), min(a + bw - 0.02, _BK_C)
            if a1 - a0 > 0.12:
                c = E.pt((a0 + a1) / 2, y0 + bh / 2, 0.0)
                i = 0.05
                ring_m = [(a0, -bd / 2), (a1, -bd / 2), (a1, bd / 2), (a0, bd / 2)]
                ring_e = [(a0 + i, -bd / 2 + i), (a1 - i, -bd / 2 + i), (a1 - i, bd / 2 - i), (a0 + i, bd / 2 - i)]
                ym = y0 + bh * 0.5
                for k in range(4):
                    k2 = (k + 1) % 4
                    for (ya, ra), (yb, rb) in (((y0, ring_e), (ym, ring_m)), ((ym, ring_m), (y0 + bh, ring_e))):
                        p = [E.pt(ra[k][0], ya, ra[k][1]), E.pt(ra[k2][0], ya, ra[k2][1]),
                             E.pt(rb[k2][0], yb, rb[k2][1]), E.pt(rb[k][0], yb, rb[k][1])]
                        u0 = ra[k][0] if k % 2 == 0 else ra[k][1]
                        u1 = ra[k2][0] if k % 2 == 0 else ra[k2][1]
                        geo.quad("sandbag", p[0], p[1], p[2], p[3],
                                 uv=[(u0, ya), (u1, ya), (u1, yb), (u0, yb)], out=c)
                geo.quad("sandbag", E.pt(ring_e[0][0], y0 + bh, ring_e[0][1]), E.pt(ring_e[1][0], y0 + bh, ring_e[1][1]),
                         E.pt(ring_e[2][0], y0 + bh, ring_e[2][1]), E.pt(ring_e[3][0], y0 + bh, ring_e[3][1]),
                         uv=[(a0, 0), (a1, 0), (a1, bd), (a0, bd)], up=True)
            a += bw
    E.box(geo, 0, _BK_C, 0, 4 * bh, -bd / 2, bd / 2, None, collide=True)


def _bk_bake_edge(geo, d, e, kind):
    st = d.st()
    t = st["t"]
    E = _BKEdge(e)
    a, b = d.edge_cells(e)
    ia, ib = d.indoor(a), d.indoor(b)
    ma, mb = _bk_side_mat(d, a), _bk_side_mat(d, b)
    exterior = ia != ib
    s = 1.0 if (ia and not ib) else -1.0                    # n direction of the outside
    lo, hi = t / 2, _BK_C - t / 2
    if kind == "wall":
        _bk_wall_piece(geo, E, lo, hi, 0, _BK_H, ma, mb, t)
    elif kind == "window":
        _bk_window(geo, d, E, e, s, ma, mb, t, exterior)
    elif kind in ("door", "arch"):
        _bk_door(geo, d, E, e, s, ma, mb, t, exterior, wide=(kind == "arch"))
    elif kind == "ruin":
        _bk_ruin(geo, d, E, e, ma, mb, t)
    elif kind == "half":
        E.box(geo, lo, hi, 0, 1.0, -t / 2, t / 2, {"n-": ma, "n+": mb, "y+": st["trim"]}, collide=True)
        E.box(geo, lo - 0.02, hi + 0.02, 1.0, 1.06, -t / 2 - 0.03, t / 2 + 0.03, st["trim"])
    elif kind == "sandbags":
        _bk_sandbags(geo, E, t)
    if kind not in _BK_WALLISH:
        return
    span = None
    if kind == "door":
        span = (_BK_C / 2 - 0.5, _BK_C / 2 + 0.5)
    elif kind == "arch":
        span = (_BK_C / 2 - 0.78, _BK_C / 2 + 0.78)
    parts = [(lo, hi)] if span is None else [(lo, span[0]), (span[1], hi)]
    # skirting boards on the room sides
    for side, indoor in ((-1.0, ia), (1.0, ib)):
        if indoor and st.get("beams") is not None and (d.finish(a if side < 0 else b) or {}).get("wall") not in (
                "concrete", "stone", "brick_yellow"):
            for p0, p1 in parts:
                E.box(geo, p0, p1, 0, 0.11, side * t / 2, side * (t / 2 + 0.018), "wood_beam")
    # stone plinth along the foot of outside walls on the ground
    if exterior and e[1] == 0 and st.get("plinth", True):
        ext = t / 2 + 0.04
        pp = [(-ext, _BK_C + ext)] if span is None else [(-ext, span[0]), (span[1], _BK_C + ext)]
        for p0, p1 in pp:
            E.box(geo, p0, p1, -0.30, 0.50, s * (t / 2 - 0.01), s * (t / 2 + 0.04), st["trim"])


def _bk_bake_pillars(geo, d, y, kinds):
    """Corner posts where walls meet: each free face takes the finish of the
    cell it looks into."""
    st = d.st()
    t = st["t"]
    h = t / 2
    verts = {}
    for (x, yy, z, o), k in kinds.items():
        if yy != y or k in ("sandbags", None):
            continue
        ends = ((x, z + 1), (x + 1, z + 1)) if o == "N" else ((x + 1, z), (x + 1, z + 1))
        for i, v in enumerate(ends):
            verts.setdefault(v, {})[(o, i)] = k
    y0 = y * _BK_H
    for (vx, vz), inc in verts.items():
        ks = list(inc.values())
        if all(k == "half" for k in ks):
            top = 1.0
        elif all(k in ("ruin", "half") for k in ks):
            top = _bk_ruin_height(d, vx, y, vz)
        else:
            top = _BK_H
        X, Z = vx * _BK_C, vz * _BK_C
        # a wall leaving the post in +x is the N edge starting here, etc.
        has = {"x+": ("N", 0) in inc, "x-": ("N", 1) in inc, "z+": ("E", 0) in inc, "z-": ("E", 1) in inc}
        quad = {(-1, -1): (vx - 1, y, vz - 1), (1, -1): (vx, y, vz - 1), (-1, 1): (vx - 1, y, vz), (1, 1): (vx, y, vz)}
        cap = "rubble" if top < _BK_H - 1e-6 else None
        if cap == "rubble" and all(k == "half" for k in ks):
            cap = st["trim"]
        c = (X, y0 + top / 2, Z)
        for face, (ax, sg) in (("x+", (0, 1)), ("x-", (0, -1)), ("z+", (2, 1)), ("z-", (2, -1))):
            if has[face]:
                continue
            for half in (-1, 1):
                q = quad[(sg, half)] if ax == 0 else quad[(half, sg)]
                m = _bk_side_mat(d, q)
                if ax == 0:
                    xx = X + sg * h
                    z0, z1 = (Z - h, Z) if half < 0 else (Z, Z + h)
                    geo.quad(m, (xx, y0, z0), (xx, y0, z1), (xx, y0 + top, z1), (xx, y0 + top, z0), out=c)
                else:
                    zz = Z + sg * h
                    x0, x1 = (X - h, X) if half < 0 else (X, X + h)
                    geo.quad(m, (x0, y0, zz), (x1, y0, zz), (x1, y0 + top, zz), (x0, y0 + top, zz), out=c)
        geo.box((X - h, y0, Z - h), (X + h, y0 + top, Z + h), {"y+": cap} if cap else None, collide=True)


def _bk_bake_stairs(geo, d, y):
    st = d.st()
    mat = st["stairs"]
    for (x, yy, z), dr in d.stairs.items():
        if yy != y:
            continue
        dx, dz = ((1, 0), (0, 1), (-1, 0), (0, -1))[dr % 4]
        n = 17
        rise = _BK_H / n
        run = (2 * _BK_C - 0.30) / n
        w = 0.60                                            # half width
        cx, cz = (x + 0.5) * _BK_C, (z + 0.5) * _BK_C
        sx, sz = cx - dx * (_BK_C / 2 - 0.10), cz - dz * (_BK_C / 2 - 0.10)        # start of the flight
        y0 = y * _BK_H
        for i in range(n):
            a0, a1 = i * run, (i + 1) * run + (0.0 if i < n - 1 else 0.2)
            lo = (sx + dx * a0 - abs(dz) * w, y0 + max(0.0, (i - 3) * rise), sz + dz * a0 - abs(dx) * w)
            hi = (sx + dx * a1 + abs(dz) * w, y0 + (i + 1) * rise, sz + dz * a1 + abs(dx) * w)
            geo.box(lo, hi, mat, collide=True)
        # handrails on posts
        for side in (-1, 1):
            ox, oz = abs(dz) * w * side, abs(dx) * w * side
            p = []
            for i in (0, n // 2, n - 1):
                a = (i + 0.5) * run
                bx, bz, by = sx + dx * a + ox, sz + dz * a + oz, y0 + (i + 1) * rise
                geo.box((bx - 0.035, by, bz - 0.035), (bx + 0.035, by + 0.92, bz + 0.035), "wood_beam")
                p.append((bx, by + 0.92, bz))
            for (ax, ay, az), (bx, by, bz) in zip(p[:-1], p[1:]):
                hw = 0.03
                wx, wz = abs(dz) * hw, abs(dx) * hw
                a_lo, a_hi = (ax - wx, ay, az - wz), (ax + wx, ay, az + wz)
                b_lo, b_hi = (bx - wx, by, bz - wz), (bx + wx, by, bz + wz)
                top = 0.06
                cc = ((ax + bx) / 2, (ay + by) / 2 + top / 2, (az + bz) / 2)
                up = lambda q: (q[0], q[1] + top, q[2])
                L = math.hypot(math.hypot(bx - ax, bz - az), by - ay)
                uv = [(0, 0), (L, 0), (L, top), (0, top)]
                geo.quad("wood_beam", a_lo, b_lo, up(b_lo), up(a_lo), uv=uv, out=cc)
                geo.quad("wood_beam", a_hi, b_hi, up(b_hi), up(a_hi), uv=uv, out=cc)
                geo.quad("wood_beam", up(a_lo), up(b_lo), up(b_hi), up(a_hi), uv=uv, out=cc)
                geo.quad("wood_beam", a_lo, b_lo, b_hi, a_hi, uv=uv, out=cc)


def _bk_slab_cells(d, L):
    """Cells with a floor slab at level L: {(x, z): (cell above or None, cell below or None)}."""
    holes = {(c[0], c[2]) for c in d.stair_cells() if c[1] == L - 1}
    out = {}
    for (x, y, z) in d.cells:
        if y == L and (x, z) not in holes:
            out.setdefault((x, z), [None, None])[0] = (x, y, z)
        elif y == L - 1 and (x, z) not in holes and d.indoor((x, y, z)) and (x, y, z) not in d.open:
            out.setdefault((x, z), [None, None])[1] = (x, y, z)
    return out


def _bk_roof_cells(d, L):
    """Cells whose roof sits at level L (indoor below, nothing above)."""
    return {(x, z) for (x, y, z) in d.cells
            if y == L - 1 and d.indoor((x, y, z)) and (x, y, z) not in d.open and (x, L, z) not in d.cells}


def _bk_bake_slabs(geo, d, L):
    st = d.st()
    t = st["t"]
    slabs = _bk_slab_cells(d, L)
    roofs = _bk_roof_cells(d, L)
    flat = d.roof_type() in ("flat", "none")
    y1 = L * _BK_H
    y0 = y1 - _BK_SLAB
    for (x, z), (up, dn) in slabs.items():
        fu = d.finish(up) if up else None
        top = fu["floor"] if fu else (st["roof"] if (flat and st["roof_type"] == "flat") else "concrete_floor")
        if up is None and (x, z) in roofs and not flat:
            top = None                                       # hidden under the pitched roof
        fd_ = d.finish(dn) if dn else None
        bottom = (fd_.get("ceil") or st["ceil"]) if fd_ else None
        X0, Z0, X1, Z1 = x * _BK_C, z * _BK_C, (x + 1) * _BK_C, (z + 1) * _BK_C
        mats = {"y+": top, "y-": bottom if L > 0 or dn else None}
        for face, nb in (("x-", (x - 1, z)), ("x+", (x + 1, z)), ("z-", (x, z - 1)), ("z+", (x, z + 1))):
            if nb not in slabs:
                mats[face] = st["trim"]
        geo.box((X0, y0, Z0), (X1, y1, Z1), mats)
        if dn and st.get("beams") and (fd_ or {}).get("ceil") is None:
            for k in (0.5, 1.5):
                geo.box((X0, y0 - 0.13, Z0 + k - 0.055), (X1, y0, Z0 + k + 0.055),
                        {"y-": "wood_beam", "z-": "wood_beam", "z+": "wood_beam"})
    # collision: merge runs of slab cells along x
    for (x, z) in sorted(slabs, key=lambda c: (c[1], c[0])):
        if (x - 1, z) in slabs:
            continue
        x2 = x
        while (x2 + 1, z) in slabs:
            x2 += 1
        geo.col.append((x * _BK_C, y0, z * _BK_C, (x2 + 1) * _BK_C, y1, (z + 1) * _BK_C))
    # string course: the slab edge shows as a stone band around the building
    if L >= 1:
        ext = t / 2 + 0.03
        solid = {c for c, (up, dn) in slabs.items() if (up and d.indoor(up)) or dn}
        solid |= {(c[0], c[2]) for c in d.stair_cells() if c[1] == L - 1}          # stair wells
        for (x, z) in solid:
            for o, e, nb, sg in (("N", (x, L, z, "N"), (x, z + 1), 1.0), ("N", (x, L, z - 1, "N"), (x, z - 1), -1.0),
                                 ("E", (x, L, z, "E"), (x + 1, z), 1.0), ("E", (x - 1, L, z, "E"), (x - 1, z), -1.0)):
                if nb in solid:
                    continue
                E = _BKEdge(e)
                E.y0 = 0.0
                E.box(geo, -ext, _BK_C + ext, y0 - 0.02, y1 + 0.02, 0.0, sg * ext, st["trim"])


def _bk_components(cells):
    cells = set(cells)
    out = []
    while cells:
        s = [cells.pop()]
        comp = set(s)
        while s:
            x, z = s.pop()
            for nb in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if nb in cells:
                    cells.discard(nb)
                    comp.add(nb)
                    s.append(nb)
        out.append(comp)
    return out


def _bk_bake_roof(geo, d, L):
    st = d.st()
    rt = d.roof_type()
    cells = _bk_roof_cells(d, L)
    if not cells or rt == "none":
        return
    t = st["t"]
    Y0 = L * _BK_H
    mat = st["roof"]
    tw_, th_ = _BK_TILE.get(mat, (2.0, 2.0))
    if rt == "flat":
        for (x, z) in cells:                                 # a parapet on the free edges
            for e, nb, sg in (((x, L, z, "N"), (x, z + 1), 1.0), ((x, L, z - 1, "N"), (x, z - 1), -1.0),
                              ((x, L, z, "E"), (x + 1, z), 1.0), ((x - 1, L, z, "E"), (x - 1, z), -1.0)):
                if nb in cells or (nb[0], L, nb[1]) in d.cells:
                    continue
                E = _BKEdge(e)
                E.y0 = Y0
                ext = t / 2
                E.box(geo, -ext, _BK_C + ext, 0, 0.45, -sg * ext if sg > 0 else 0.0, sg * ext if sg > 0 else -ext,
                      {"n-": st["ext"], "n+": st["ext"], "y+": st["trim"], "a-": st["ext"], "a+": st["ext"]},
                      collide=True)
        return
    tan = math.tan(math.radians(d.roof_pitch()))
    cosp = math.cos(math.radians(d.roof_pitch()))
    over = 0.35
    for comp in _bk_components(cells):
        xs, zs = [c[0] for c in comp], [c[1] for c in comp]
        x0, x1, z0, z1 = min(xs), max(xs) + 1, min(zs), max(zs) + 1
        rect = len(comp) == (x1 - x0) * (z1 - z0)
        free = lambda nb: nb not in comp and (nb[0], L, nb[1]) not in d.cells
        ridge = 0.0
        if rt == "gable" and rect and all(free(nb) for c in comp for nb in ((c[0] + 1, c[1]), (c[0] - 1, c[1]),
                                                                        (c[0], c[1] + 1), (c[0], c[1] - 1))
                                          if nb not in comp):
            along_x = (x1 - x0) >= (z1 - z0)                 # the ridge follows the long side
            if along_x:
                a0, a1, b0, b1 = x0 * _BK_C, x1 * _BK_C, z0 * _BK_C, z1 * _BK_C
                P = lambda a, y, b: (a, y, b)
            else:
                a0, a1, b0, b1 = z0 * _BK_C, z1 * _BK_C, x0 * _BK_C, x1 * _BK_C
                P = lambda a, y, b: (b, y, a)
            bc = (b0 + b1) / 2
            half = (b1 - b0) / 2 + t / 2
            ridge = half * tan
            va, vb = a0 - t / 2 - 0.30, a1 + t / 2 + 0.30     # verge overhang past the gable walls
            sl = (half + over) / cosp
            for sg in (-1.0, 1.0):
                be = bc + sg * (half + over)
                geo.quad(mat, P(va, Y0 - over * tan, be), P(vb, Y0 - over * tan, be), P(vb, Y0 + ridge, bc),
                         P(va, Y0 + ridge, bc), uv=[(va, 0), (vb, 0), (vb, sl), (va, sl)], up=True)
                geo.col_tris.extend(P(va, Y0 - over * tan, be) + P(vb, Y0 - over * tan, be) + P(vb, Y0 + ridge, bc))
                geo.col_tris.extend(P(va, Y0 - over * tan, be) + P(vb, Y0 + ridge, bc) + P(va, Y0 + ridge, bc))
            for ag, sg in ((a0 - t / 2, -1.0), (a1 + t / 2, 1.0)):                    # the gable walls
                p0, p1, p2 = P(ag, Y0 - 0.02, bc - half), P(ag, Y0 - 0.02, bc + half), P(ag, Y0 + ridge - 0.02, bc)
                uvw = [(bc - half, Y0), (bc + half, Y0), (bc, Y0 + ridge)]
                outp = P(ag - sg, Y0, bc)
                geo.tri(st["ext"], p0, p1, p2, uvw, out=outp)
                geo.tri(st["ceil"], P(ag - sg * 0.02, Y0, bc - half), P(ag - sg * 0.02, Y0, bc + half),
                        P(ag - sg * 0.02, Y0 + ridge, bc), uvw, out=P(ag + sg, Y0, bc))
            # ridge capping
            geo.box(P(va, Y0 + ridge - 0.02, bc - 0.09), P(vb, Y0 + ridge + 0.05, bc + 0.09), st["trim"])
            top_at = lambda px, pz: Y0 + max(0.0, (half - abs((pz if along_x else px) - bc))) * tan
        else:
            # hipped roof over any footprint: height = Chebyshev distance to the open edge
            air = [(x, z) for x in range(x0 - 1, x1 + 1) for z in range(z0 - 1, z1 + 1)
                   if (x, z) not in comp and (x, L, z) not in d.cells]
            if not air:
                continue
            A = np.asarray(air, np.float64)

            def dist(px, pz):                               # in cells
                ddx = np.maximum(np.maximum(A[:, 0] - px, px - (A[:, 0] + 1)), 0.0)
                ddz = np.maximum(np.maximum(A[:, 1] - pz, pz - (A[:, 1] + 1)), 0.0)
                return float(np.maximum(ddx, ddz).min())
            hgt = {}

            def hh(px, pz):
                k = (round(px * 40), round(pz * 40))
                v = hgt.get(k)
                if v is None:
                    v = hgt[k] = (dist(px, pz) * _BK_C + t / 2) * tan
                return v
            for (x, z) in comp:
                for qx in (0, 1):
                    for qz in (0, 1):
                        ax, az = x + qx * 0.5, z + qz * 0.5
                        cn = [(ax, az), (ax + 0.5, az), (ax + 0.5, az + 0.5), (ax, az + 0.5)]
                        mid = (ax + 0.25, az + 0.25)
                        pm = (mid[0] * _BK_C, Y0 + hh(*mid), mid[1] * _BK_C)
                        for k in range(4):
                            c0, c1 = cn[k], cn[(k + 1) % 4]
                            p0 = (c0[0] * _BK_C, Y0 + hh(*c0), c0[1] * _BK_C)
                            p1 = (c1[0] * _BK_C, Y0 + hh(*c1), c1[1] * _BK_C)
                            # slope direction: the horizontal axis along which the height changes most
                            gx = abs((hh(mid[0] + 0.05, mid[1]) - hh(mid[0] - 0.05, mid[1])))
                            gz = abs((hh(mid[0], mid[1] + 0.05) - hh(mid[0], mid[1] - 0.05)))
                            if gx >= gz:
                                uv = [(p[2], (p[1] - Y0) / max(tan, 1e-6) / cosp) for p in (p0, p1, pm)]
                            else:
                                uv = [(p[0], (p[1] - Y0) / max(tan, 1e-6) / cosp) for p in (p0, p1, pm)]
                            geo.tri(mat, p0, p1, pm, uv, up=True)
                            ridge = max(ridge, p0[1] - Y0, pm[1] - Y0)
                geo.col.append((x * _BK_C, Y0, z * _BK_C, (x + 1) * _BK_C, Y0 + 0.1, (z + 1) * _BK_C))
            # eaves: the roof runs on past the walls on every open edge
            base = t / 2 * tan
            drop = over * tan
            for (x, z) in comp:
                for nb, (px0, pz0, px1, pz1, nx, nz) in (((x, z + 1), (x, z + 1, x + 1, z + 1, 0, 1)),
                                                         ((x, z - 1), (x, z, x + 1, z, 0, -1)),
                                                         ((x + 1, z), (x + 1, z, x + 1, z + 1, 1, 0)),
                                                         ((x - 1, z), (x, z, x, z + 1, -1, 0))):
                    if not free(nb):
                        continue
                    e0 = (px0 * _BK_C, pz0 * _BK_C)
                    e1 = (px1 * _BK_C, pz1 * _BK_C)
                    ex = t / 2 + over
                    # widen along the edge at free ends so neighbouring strips meet at the corners
                    tx, tz = (1, 0) if nz else (0, 1)
                    lo_nb = (nb[0] - tx, nb[1] - tz)
                    hi_nb = (nb[0] + tx, nb[1] + tz)
                    lo_in = (x - tx, z - tz) in comp
                    hi_in = (x + tx, z + tz) in comp
                    l0 = 0.0 if (lo_in or not free(lo_nb)) else ex
                    l1 = 0.0 if (hi_in or not free(hi_nb)) else ex
                    i0 = (e0[0], Y0 + base, e0[1])
                    i1 = (e1[0], Y0 + base, e1[1])
                    o0 = (e0[0] + nx * ex - tx * l0, Y0 - drop, e0[1] + nz * ex - tz * l0)
                    o1 = (e1[0] + nx * ex + tx * l1, Y0 - drop, e1[1] + nz * ex + tz * l1)
                    u = lambda p: p[0] if nz else p[2]
                    geo.quad(mat, i0, i1, o1, o0, uv=[(u(i0), 0), (u(i1), 0), (u(o1), -ex / cosp), (u(o0), -ex / cosp)],
                             up=True)
            top_at = lambda px, pz: Y0 + hh(px / _BK_C, pz / _BK_C)
        # a brick chimney through the roof
        if st.get("chimney") and len(comp) >= 4:
            cs = sorted(comp)
            cx_, cz_ = cs[int(_bk_hash("ch", d.seed, L, len(comp)) * len(cs)) % len(cs)]
            px, pz = (cx_ + 0.5) * _BK_C, (cz_ + 0.5) * _BK_C
            ytop = max(top_at(px, pz) + 0.9, Y0 + ridge * 0.6 + 0.9)
            geo.box((px - 0.34, Y0, pz - 0.34), (px + 0.34, ytop, pz + 0.34),
                    {k: "brick_red" if st["ext"].startswith("plaster") else st["ext"] for k in ("x-", "x+", "z-", "z+")},
                    collide=True)
            geo.box((px - 0.42, ytop, pz - 0.42), (px + 0.42, ytop + 0.09, pz + 0.42), st["trim"])
            geo.box((px - 0.20, ytop + 0.09, pz - 0.20), (px + 0.20, ytop + 0.34, pz + 0.20), "roof_tiles")


def bk_bake_level(d, L):
    """Everything at level L: the walls / stairs of storey L, the floor slab
    at its foot (also the ceiling of the storey below) and roofs resting at
    that height.  -> _BKGeo"""
    geo = _BKGeo()
    kinds = {}
    seen = set()
    for (x, y, z) in d.cells:
        if y != L:
            continue
        for e in ((x, y, z, "N"), (x, y, z, "E"), (x, y, z - 1, "N"), (x - 1, y, z, "E")):
            seen.add(e)
    for e in d.edges:
        if e[1] == L:
            seen.add(e)
    for e in seen:
        k = d.edge_kind(e)
        if k:
            kinds[e] = k
            _bk_bake_edge(geo, d, e, k)
    _bk_bake_pillars(geo, d, L, kinds)
    _bk_bake_stairs(geo, d, L)
    _bk_bake_slabs(geo, d, L)
    _bk_bake_roof(geo, d, L)
    return geo


# ---------------------------------------------------------------------------
#  scene objects
# ---------------------------------------------------------------------------
_BK_MATERIAL_CACHE = {}


def bk_material(name):
    """The shared Material of a library material (textures made on first use)."""
    m = _BK_MATERIAL_CACHE.get(name)
    if m is not None:
        return m
    t0 = time.time()
    try:
        f = bk_material_files(name)
    except Exception as ex:
        Log.error(f"[Building Maker] material '{name}' failed: {ex}")
        f = None
    kw = {}
    if f is not None:
        kw = {"base_color_tex": ("path", f["albedo"]), "normal_tex": ("path", f["normal"]),
              "metallic_roughness_tex": ("path", f["mr"]), "occlusion_tex": ("path", f["ao"])}
    metal = name in ("corrugated", "steel")
    m = Material(name="bk_" + name, base_color=(1.0, 1.0, 1.0, 1.0), metallic=1.0 if metal else 0.0, roughness=1.0,
                 double_sided=name in ("glass", "roof_tiles", "roof_slate", "corrugated", "sandbag", "rubble"),
                 alpha_mode="BLEND" if name == "glass" else "OPAQUE", **kw)
    if name == "glass":
        m.base_color = (1.0, 1.0, 1.0, 0.9)
    _BK_MATERIAL_CACHE[name] = m
    if time.time() - t0 > 0.3:
        Log.info(f"[Building Maker] made the '{name}' material ({time.time() - t0:.1f}s, cached for next time)")
    return m


class _BKProxy:
    """What the physics needs from a mesh: CPU triangles."""

    def __init__(self, pos, idx):
        self.cpu_positions = pos
        self.cpu_indices = idx
        self.vertex_count = len(pos)
        self.index_count = len(idx)
        self.indexed = True
        self._vertex_data = None
        lo, hi = (pos.min(0), pos.max(0)) if len(pos) else (np.zeros(3), np.zeros(3))
        c = (lo + hi) * 0.5
        self.bounding_box = (glm.vec3(*[float(v) for v in lo]), glm.vec3(*[float(v) for v in hi]))
        self.bounding_sphere = (glm.vec3(*[float(v) for v in c]),
                                float(np.linalg.norm(pos - c, axis=1).max()) if len(pos) else 0.0)


def bk_is_building(node):
    return node is not None and getattr(node, "mesh_type", None) == _BK_MESH_TYPE


def bk_data(node):
    d = getattr(node, "_bk", None)
    if d is None:
        d = node._bk = BKData(node.mesh_params if isinstance(node.mesh_params, dict) else None)
    return d


def _bk_free_children(node, level=None):
    for c in list(node.children):
        lv = getattr(c, "_bk_level", None)
        if lv is None or (level is not None and lv != level):
            continue
        m = c.mesh
        c.mesh = None
        c.remove()
        try:
            if m is not None and hasattr(m, "release"):
                m.release()
        except Exception:
            pass


def bk_rebuild(engine, node, levels=None):
    """(Re)bake a building's meshes and collision.  ``levels``: only these
    levels (an edit on storey y touches levels y and y + 1)."""
    d = bk_data(node)
    node.mesh_type = _BK_MESH_TYPE
    node.mesh_params = d.to_params()
    node.mesh = None
    ctx = getattr(engine, "ctx", None)
    lo, hi = d.levels()
    wanted = set(range(lo, hi + 2)) if d.cells else set()
    cols = getattr(node, "_bk_cols", None)
    if cols is None or levels is None:
        cols = node._bk_cols = {}
        _bk_free_children(node)
        todo = wanted
    else:
        todo = {L for L in levels if L in wanted}
        for L in set(levels) | (set(cols) - wanted):
            _bk_free_children(node, L)
            cols.pop(L, None)
    tris = 0
    for L in sorted(todo):
        geo = bk_bake_level(d, L)
        cols[L] = geo.collision()
        if ctx is None:
            continue
        for mat, (pos, nrm, uv, idx) in sorted(geo.arrays().items()):
            child = SceneNode(f"{node.name}_L{L}_{mat}", parent=node)
            child._heaven3d_imported_part = "building"
            child._h3d_locked = True
            child._bk_level = L
            child.mesh = Mesh.from_arrays(ctx, pos, nrm, uv, idx, name=child.name)
            child.material = bk_material(mat)
            child.mesh._h3d_cull = not child.material.double_sided
            tris += len(idx) // 3
    parts = [cols[L] for L in sorted(cols) if len(cols[L][1])]
    if parts:
        off = 0
        P, I = [], []
        for pos, idx in parts:
            P.append(pos)
            I.append(idx + off)
            off += len(pos)
        node._bk_proxy = _BKProxy(np.concatenate(P), np.concatenate(I))
        if not isinstance(node.collider, MeshCollider):
            node.collider = MeshCollider()
        node.collider._mesh_cache = None
        node.rigidbody = None
    else:
        node._bk_proxy = None
        node.collider = None
    bk_apply_view(node)
    try:
        engine.physics._statics_dirty = True
    except Exception:
        pass
    return node


def bk_apply_view(node, top=_BK_KEEP):
    """Hide the storeys above ``top`` (None: show everything) so the level
    being edited can be seen from above.  Without ``top``: re-apply the
    building's current setting (after a re-bake)."""
    if top is not _BK_KEEP:
        node._bk_view_top = top
    top = getattr(node, "_bk_view_top", None)
    for c in node.children:
        lv = getattr(c, "_bk_level", None)
        if lv is not None:
            c.visible = top is None or lv <= top


def bk_new(engine, style="town", position=(0.0, 0.0, 0.0), name=None, seed=None):
    """An empty building at ``position`` (snapped to the 2 m grid)."""
    if style not in BK_STYLES:
        style = "town"
    base = name or ("Building_" + style)
    try:
        base = engine._unique_node_name(base)
    except Exception:
        pass
    node = SceneNode(base, parent=engine.scene.root)
    node.position = glm.vec3(round(float(position[0]) / _BK_C) * _BK_C, float(position[1]),
                             round(float(position[2]) / _BK_C) * _BK_C)
    node._bk = BKData({"style": style, "seed": int(seed if seed is not None else time.time() * 1000) % 1000003})
    return bk_rebuild(engine, node)


def bk_stats(node):
    tris = sum(int(c.mesh.index_count) // 3 for c in node.children
               if getattr(c, "_bk_level", None) is not None and c.mesh is not None)
    parts = sum(1 for c in node.children if getattr(c, "_bk_level", None) is not None)
    px = getattr(node, "_bk_proxy", None)
    return {"cells": len(bk_data(node).cells), "triangles": tris, "meshes": parts,
            "collision_triangles": 0 if px is None else px.index_count // 3}


# ---------------------------------------------------------------------------
#  a quick house (the "Generate" button, and scripts)
# ---------------------------------------------------------------------------
def bk_generate_house(d, w=4, depth=3, floors=2, damage=0.0, seed=None, front=0):
    """Fill ``d`` with a plausible house: rooms off a hall, a front door,
    windows on every outside wall, a stair when it has an upper floor."""
    seed = d.seed if seed is None else int(seed)
    d.seed = seed
    d.cells.clear(); d.edges.clear(); d.stairs.clear(); d.open.clear()
    w, depth, floors = max(2, int(w)), max(2, int(depth)), max(1, int(floors))
    nfin = len(d.finishes)
    split = max(1, min(w - 1, int(round(w * (0.4 + 0.2 * _bk_hash("sp", seed))))))
    for y in range(floors):
        for x in range(w):
            for z in range(depth):
                room = 0 if x < split else 1
                if depth >= 4 and z >= depth // 2 and x >= split:
                    room = 2
                d.cells[(x, y, z)] = (room + y) % nfin
    stair = None
    if floors > 1 and depth >= 3:
        stair = (0, depth - 3) if w >= 2 else None
    for y in range(floors):
        # doors between the rooms
        zz = int(_bk_hash("d1", seed, y) * depth) % depth
        if split < w and d.cells[(split - 1, y, zz)] != d.cells[(split, y, zz)]:
            d.edges[(split - 1, y, zz, "E")] = "door"
        if depth >= 4 and w - split >= 1:
            xx = split + int(_bk_hash("d2", seed, y) * (w - split)) % (w - split)
            z0 = depth // 2 - 1
            if d.cells[(xx, y, z0)] != d.cells[(xx, y, z0 + 1)]:
                d.edges[(xx, y, z0, "N")] = "door"
        # windows on the outside walls
        for x in range(w):
            for z, e in ((0, (x, y, -1, "N")), (depth - 1, (x, y, depth - 1, "N"))):
                if _bk_hash("w", seed, x, y, z) > 0.25:
                    d.edges[e] = "window"
        for z in range(depth):
            for x, e in ((0, (-1, y, z, "E")), (w - 1, (w - 1, y, z, "E"))):
                if _bk_hash("w", seed, x, y, z, 9) > 0.45:
                    d.edges[e] = "window"
        if stair and y < floors - 1:
            d.stairs[(stair[0], y, stair[1])] = 1
            for k in (0, 1):                                 # no window behind the flight
                d.edges.pop((-1, y, stair[1] + k, "E"), None)
    fx = int(_bk_hash("fd", seed) * w) % w                   # the front door (front=1: on the far side)
    d.edges[(fx, 0, depth - 1 if front else -1, "N")] = "door"
    if damage > 0:
        # shell damage: a corner of the top storey is blown out
        top = floors - 1
        cx, cz = (w - 1 if _bk_hash("dx", seed) > 0.5 else 0), (depth - 1 if _bk_hash("dz", seed) > 0.5 else 0)
        r = max(1, int(round(max(w, depth) * damage)))
        for x in range(w):
            for z in range(depth):
                if max(abs(x - cx), abs(z - cz)) < r and (stair is None or (x, z) not in (stair, (stair[0], stair[1] + 1))):
                    d.open.add((x, top, z))
                    for e in ((x, top, z, "N"), (x, top, z, "E"), (x, top, z - 1, "N"), (x - 1, top, z, "E")):
                        a, b = d.edge_cells(e)
                        if (a in d.cells) != (b in d.cells):
                            d.edges[e] = "ruin"
    return d


# ---------------------------------------------------------------------------
#  engine hooks: load, collision, script API
# ---------------------------------------------------------------------------
def _bk_add_building(self, style="town", position=(0.0, 0.0, 0.0), house=None, name=None, seed=None):
    """engine.add_building("town", (0, 0, 0), house=(4, 3, 2)) -> the building
    object.  ``house``: (width, depth, floors[, damage 0..1]) in cells, or
    None for an empty building to fill with engine.building_edit()."""
    node = bk_new(self, style, position, name, seed)
    if house:
        bk_generate_house(bk_data(node), *house)
        bk_rebuild(self, node)
    return node


def _bk_building_edit(self, node, cells=None, erase=None, edges=None, stairs=None, open_cells=None, finish=None):
    """Change a building from a script and re-bake what changed.
    cells / erase: [(x, y, z), ...]; edges: {(x, y, z, "N"|"E"): kind};
    stairs: {(x, y, z): direction or None}; finish: a BK_FINISHES name."""
    d = bk_data(node)
    lv = set()
    fi = d.finish_index(finish) if finish else 0
    for c in (cells or ()):
        c = tuple(int(v) for v in c)
        d.cells[c] = fi
        lv.update((c[1], c[1] + 1))
    for c in (erase or ()):
        c = tuple(int(v) for v in c)
        if d.cells.pop(c, None) is not None:
            d.open.discard(c)
            d.stairs.pop(c, None)
            lv.update((c[1], c[1] + 1))
    for e, k in (edges or {}).items():
        e = (int(e[0]), int(e[1]), int(e[2]), str(e[3]))
        if k is None:
            d.edges.pop(e, None)
        else:
            d.edges[e] = str(k)
        lv.update((e[1], e[1] + 1))
    for c, dr in (stairs or {}).items():
        c = tuple(int(v) for v in c)
        if dr is None:
            d.stairs.pop(c, None)
        else:
            d.stairs[c] = int(dr) % 4
        lv.update((c[1], c[1] + 1))
    for c in (open_cells or ()):
        c = tuple(int(v) for v in c)
        d.open.add(c)
        lv.update((c[1], c[1] + 1))
    bk_rebuild(self, node, lv or None)
    return node


if not globals().get("_BK_INSTALLED"):
    _BK_INSTALLED = True
    HeavenEngine.add_building = _bk_add_building
    HeavenEngine.building_edit = _bk_building_edit

    _BK_PREV_DESERIALIZE = ProjectSerializer._deserialize_node.__func__

    def _bk_deserialize_node(cls, data, parent, engine, project_dir):
        before = len(parent.children)
        _BK_PREV_DESERIALIZE(cls, data, parent, engine, project_dir)
        try:
            if (isinstance(data, dict) and data.get("mesh_type") == _BK_MESH_TYPE
                    and len(parent.children) > before):
                node = parent.children[-1]
                node._bk = None
                bk_rebuild(engine, node)
        except Exception as ex:
            Log.error(f"[Building Maker] could not rebuild a building: {ex}")
            traceback.print_exc()

    ProjectSerializer._deserialize_node = classmethod(_bk_deserialize_node)

    # a saved project carries the textures of its buildings (exports copy the project folder)
    _BK_PREV_SAVE_PROJECT = HeavenEngine.save_project

    def _bk_save_project(self, path=None):
        r = _BK_PREV_SAVE_PROJECT(self, path)
        try:
            used = {c.material.name[3:] for n in self.scene.root.traverse() if bk_is_building(n)
                    for c in n.children if c.material is not None and str(c.material.name).startswith("bk_")}
            if used:
                k = bk_store_project_textures(used)
                if k:
                    Log.info(f"[Building Maker] {k} texture files copied into the project (assets/{_BK_PROJECT_TEX})")
        except Exception as ex:
            Log.warn(f"[Building Maker] textures not copied into the project: {ex}")
        return r

    HeavenEngine.save_project = _bk_save_project

    # clicking any part of a building selects the building
    _BK_PREV_SELECT_NODE = HeavenEngine.select_node

    def _bk_select_node(self, node):
        if node is not None and getattr(node, "_bk_level", None) is not None and bk_is_building(node.parent):
            node = node.parent
        return _BK_PREV_SELECT_NODE(self, node)

    HeavenEngine.select_node = _bk_select_node

    # the collision mesh of a building is its own low-poly proxy
    _BK_PREV_SHAPE_VIEW = globals().get("_H3D27_PREV_SHAPE_VIEW") or _mesh_shape_view
    _BK_NEXT_SHAPE_VIEW = _mesh_shape_view

    def _mesh_shape_view(node, col):
        proxy = getattr(node, "_bk_proxy", None)
        if proxy is None or getattr(node, "mesh_type", None) != _BK_MESH_TYPE:
            return _BK_NEXT_SHAPE_VIEW(node, col)
        real = node.mesh
        node.mesh = proxy
        try:
            return _BK_PREV_SHAPE_VIEW(node, col)
        finally:
            node.mesh = real

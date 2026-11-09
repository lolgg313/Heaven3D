# @heaven3d-part world/material_graph.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [MATERIAL GRAPH]  Node materials (.vmat), Blender-style
#
#   A .vmat file is a material asset.  It holds a node graph (the editor's
#   Material tab) that ends in a "Material Output" node.  The graph is
#   evaluated per texel with numpy and BAKED into ordinary PBR textures
#   (base colour + alpha, metallic/roughness, normal, emission) next to the
#   .vmat in a hidden .baked/ folder.  The renderer then draws it like any
#   imported material - no special shader, the same look in the editor and
#   in exported games, and nothing to compile at run time.  Inputs that
#   stay constant are not baked at all; they become plain material values.
#
#   Every procedural texture tiles seamlessly (noise, voronoi, bricks ...).
#
#     engine.assign_material_asset(node, "assets/materials/Rock.vmat")
#     engine.refresh_material_asset(path)     # re-bake + update every user
#     material_asset_build(path)              # -> material values (+ textures)
#
#   Objects remember their material asset (saved in projects and prefabs);
#   when the .vmat changes, loading the project picks the change up.
# ---------------------------------------------------------------------------
import hashlib as _mg_hashlib

MATERIAL_EXT = ".vmat"
MG_DEFAULT_RES = 512
_MG_IMG_CACHE = {}


# ---- value helpers (a value is a constant or an (N, N[, C]) numpy array) ----
def _mg_is_arr(x):
    return isinstance(x, np.ndarray)


def _mg_f(x):
    """Any value -> float or (N, N) array."""
    if _mg_is_arr(x):
        if x.ndim == 3:
            return x[..., :3].mean(axis=2)
        return x
    if isinstance(x, (tuple, list)):
        vals = [float(v) for v in list(x)[:3]]
        return sum(vals) / max(1, len(vals))
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _mg_c(x):
    """Any value -> (r, g, b) tuple or (N, N, 3) array."""
    if _mg_is_arr(x):
        if x.ndim == 2:
            return np.repeat(x[..., None], 3, axis=2)
        return x[..., :3]
    if isinstance(x, (tuple, list)):
        v = [float(a) for a in list(x)[:3]]
        while len(v) < 3:
            v.append(v[-1] if v else 0.0)
        return tuple(v)
    f = _mg_f(x)
    return (f, f, f)


def _mg_carr(c, n):
    c = _mg_c(c)
    if _mg_is_arr(c):
        return c
    return np.broadcast_to(np.array(c, dtype=np.float32), (n, n, 3))


def _mg_farr(f, n):
    f = _mg_f(f)
    if _mg_is_arr(f):
        return f
    return np.full((n, n), f, dtype=np.float32)


def _mg_lerp(a, b, t):
    """a + (b - a) * t for any mix of constants, colours and arrays."""
    if not any(_mg_is_arr(x) for x in (a, b, t)):
        if isinstance(a, tuple) or isinstance(b, tuple):
            ca, cb = _mg_c(a), _mg_c(b)
            return tuple(x + (y - x) * t for x, y in zip(ca, cb))
        return a + (b - a) * t
    A = np.asarray(a, dtype=np.float32)
    B = np.asarray(b, dtype=np.float32)
    T = np.asarray(t, dtype=np.float32)
    if T.ndim == 2 and (A.ndim in (1, 3) or B.ndim in (1, 3)):
        T = T[..., None]
    return A + (B - A) * T


def _mg_math(fn):
    def run(i, ctx):
        a, b = i["A"], i["B"]
        if isinstance(a, tuple) or isinstance(b, tuple) or (_mg_is_arr(a) and a.ndim == 3) or \
                (_mg_is_arr(b) and b.ndim == 3):
            a, b = _mg_c(a), _mg_c(b)
            a = np.asarray(a, dtype=np.float32) if isinstance(a, tuple) else a
            b = np.asarray(b, dtype=np.float32) if isinstance(b, tuple) else b
            r = fn(a, b)
            return {"Result": tuple(float(v) for v in r) if r.ndim == 1 else r}
        return {"Result": fn(_mg_f(a), _mg_f(b))}
    return run


# ---- tileable procedural textures ----------------------------------------------
def _mg_rand(seed, shape):
    return np.random.default_rng(int(seed) & 0x7fffffff).random(shape).astype(np.float32)


def _mg_value_noise(u, v, period, seed):
    p = max(1, int(round(period)))
    lat = _mg_rand(seed * 7919 + p, (p, p))
    x, y = u * p, v * p
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - x0, y - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    x0 %= p
    y0 %= p
    x1, y1 = (x0 + 1) % p, (y0 + 1) % p
    a = lat[y0, x0] + (lat[y0, x1] - lat[y0, x0]) * fx
    b = lat[y1, x0] + (lat[y1, x1] - lat[y1, x0]) * fx
    return a + (b - a) * fy


def _mg_fbm(ctx, scale, detail, rough, seed):
    u, v = ctx["u"], ctx["v"]
    total = np.zeros_like(u)
    amp, norm = 1.0, 0.0
    octaves = max(1, min(8, int(round(detail))))
    for o in range(octaves):
        total += _mg_value_noise(u, v, max(1.0, scale) * (2 ** o), seed + o * 101) * amp
        norm += amp
        amp *= max(0.0, min(1.0, rough))
    return total / max(1e-6, norm)


def _mg_voronoi(ctx, scale, seed):
    u, v = ctx["u"], ctx["v"]
    p = max(1, int(round(scale)))
    pts = _mg_rand(seed * 31 + p, (p, p, 2))
    cols = _mg_rand(seed * 53 + p + 1, (p, p, 3))
    x, y = u * p, v * p
    cx, cy = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    best = np.full(u.shape, 1e9, dtype=np.float32)
    bcol = np.zeros(u.shape + (3,), dtype=np.float32)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            gx, gy = cx + ox, cy + oy
            px = pts[gy % p, gx % p]
            dx = gx + px[..., 0] - x
            dy = gy + px[..., 1] - y
            d = np.sqrt(dx * dx + dy * dy)
            m = d < best
            best = np.where(m, d, best)
            bcol = np.where(m[..., None], cols[gy % p, gx % p], bcol)
    return np.clip(best, 0, 1.5) / 1.5, bcol


def _mg_bricks(ctx, scale, mortar, seed):
    u, v = ctx["u"], ctx["v"]
    rows = max(1, int(round(scale)))
    cols = max(1, rows // 2)
    y = v * rows
    row = np.floor(y).astype(np.int64)
    x = u * cols + 0.5 * (row % 2)
    col = np.floor(x).astype(np.int64)
    fx, fy = x - col, y - row
    m = max(0.0, min(0.45, mortar))
    edge = np.minimum(np.minimum(fx, 1 - fx) * 2.0, np.minimum(fy, 1 - fy))   # bricks are 2:1
    fac = (edge > m).astype(np.float32)
    rnd = _mg_rand(seed * 17 + rows, (rows, cols + 1))
    var = rnd[row % rows, col % (cols + 1)]
    return fac, var


def _mg_image(path, base_dir, n, scale):
    if not path:
        return None
    p = str(path).strip().strip('"')
    if not os.path.isabs(p) and base_dir:
        p = os.path.join(base_dir, *p.replace("\\", "/").split("/"))
    if not os.path.isfile(p):
        return None
    key = (os.path.abspath(p), os.path.getmtime(p))
    img = _MG_IMG_CACHE.get(key)
    if img is None:
        s = pygame.image.load(p)
        try:
            s = s.convert_alpha() if pygame.display.get_init() and pygame.display.get_surface() else s
        except pygame.error:
            pass
        rgb = pygame.surfarray.array3d(s).transpose(1, 0, 2).astype(np.float32) / 255.0
        try:
            a = pygame.surfarray.array_alpha(s).T.astype(np.float32) / 255.0
        except (ValueError, pygame.error):
            a = np.ones(rgb.shape[:2], dtype=np.float32)
        img = np.concatenate([rgb, a[..., None]], axis=2)
        _MG_IMG_CACHE.clear()               # keep one generation of images only
        _MG_IMG_CACHE[key] = img
    h, w = img.shape[:2]
    s = max(1e-3, float(scale))
    k = (np.arange(n, dtype=np.float32) + 0.5) / n * s
    xs = (k * w - 0.5) % w
    ys = (k * h - 0.5) % h
    x0, y0 = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    fx, fy = (xs - x0)[None, :, None], (ys - y0)[:, None, None]
    x1, y1 = (x0 + 1) % w, (y0 + 1) % h
    r0 = img[y0][:, x0] * (1 - fx) + img[y0][:, x1] * fx
    r1 = img[y1][:, x0] * (1 - fx) + img[y1][:, x1] * fx
    return r0 * (1 - fy) + r1 * fy


def _mg_normal_from_height(h, strength):
    h = h.astype(np.float32)
    n = h.shape[0]
    k = float(strength) * n / 64.0
    gx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5
    gy = (np.roll(h, 1, axis=0) - np.roll(h, -1, axis=0)) * 0.5     # image "up" = +Y (OpenGL / glTF)
    nx, ny, nz = -gx * k, -gy * k, np.ones_like(h)
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    return np.stack([nx / ln, ny / ln, nz / ln], axis=2)


# ---- the node library ------------------------------------------------------------------
#   path: {"inputs": [(name, type, default)], "outputs": [(name, type)],
#          "fn": fn(inputs, ctx) -> outputs, "category": ..., "doc": ...}
MATERIAL_NODES = {}


def material_node(path, inputs=(), outputs=(), category="default", doc=""):
    def deco(fn):
        MATERIAL_NODES[path] = {"inputs": list(inputs), "outputs": list(outputs), "fn": fn,
                                "category": category, "doc": doc or (fn.__doc__ or "").strip()}
        return fn
    return deco


_C = "color"
_F = "float"
WHITE, BLACK = (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)


@material_node("Output/Material Output",
               [("Base Color", _C, (0.8, 0.8, 0.8, 1.0)), ("Metallic", _F, 0.0), ("Roughness", _F, 0.5),
                ("Normal", "vector", None), ("Emission", _C, BLACK), ("Emission Strength", _F, 1.0),
                ("Alpha", _F, 1.0)], [], "output",
               "The finished material.  Anything left unconnected stays a plain value (not baked).")
def _mg_out(i, ctx):
    return {}


@material_node("Input/Color", [("Color", _C, (0.8, 0.3, 0.2, 1.0))], [("Color", _C)], "input")
def _mg_color(i, ctx):
    return {"Color": _mg_c(i["Color"])}


@material_node("Input/Value", [("Value", _F, 0.5)], [("Value", _F)], "input")
def _mg_value(i, ctx):
    return {"Value": _mg_f(i["Value"])}


@material_node("Input/UV", [("Scale U", _F, 1.0), ("Scale V", _F, 1.0)], [("U", _F), ("V", _F)], "input",
               "Texture coordinates (0..1 across the texture), for gradients and masks.")
def _mg_uv(i, ctx):
    return {"U": (ctx["u"] * _mg_f(i["Scale U"])) % 1.0, "V": (ctx["v"] * _mg_f(i["Scale V"])) % 1.0}


@material_node("Texture/Image", [("File", "string", ""), ("Tiling", _F, 1.0)],
               [("Color", _C), ("Alpha", _F)], "input",
               "An image file (relative to the .vmat or absolute).  Drop an image from Assets onto the graph.")
def _mg_img(i, ctx):
    img = _mg_image(i["File"], ctx.get("base_dir"), ctx["n"], _mg_f(i["Tiling"]))
    if img is None:
        return {"Color": (1.0, 0.0, 1.0), "Alpha": 1.0}
    return {"Color": img[..., :3], "Alpha": img[..., 3]}


@material_node("Texture/Noise", [("Scale", _F, 4.0), ("Detail", "int", 5), ("Roughness", _F, 0.5),
                                 ("Seed", "int", 1)], [("Fac", _F), ("Color", _C)], "color",
               "Seamless fractal noise (clouds, dirt, rock variation).")
def _mg_noise(i, ctx):
    s = _mg_f(i["Scale"])
    f = _mg_fbm(ctx, s, _mg_f(i["Detail"]), _mg_f(i["Roughness"]), int(_mg_f(i["Seed"])))
    g = _mg_fbm(ctx, s, _mg_f(i["Detail"]), _mg_f(i["Roughness"]), int(_mg_f(i["Seed"])) + 977)
    b = _mg_fbm(ctx, s, _mg_f(i["Detail"]), _mg_f(i["Roughness"]), int(_mg_f(i["Seed"])) + 1931)
    return {"Fac": f, "Color": np.stack([f, g, b], axis=2)}


@material_node("Texture/Voronoi", [("Scale", _F, 6.0), ("Seed", "int", 1)],
               [("Distance", _F), ("Color", _C)], "color", "Seamless cells (stones, scales, cracks).")
def _mg_voro(i, ctx):
    d, c = _mg_voronoi(ctx, _mg_f(i["Scale"]), int(_mg_f(i["Seed"])))
    return {"Distance": d, "Color": c}


@material_node("Texture/Checker", [("Scale", _F, 8.0), ("Color A", _C, WHITE), ("Color B", _C, (0.2, 0.2, 0.2, 1))],
               [("Color", _C), ("Fac", _F)], "color")
def _mg_checker(i, ctx):
    s = max(1, int(round(_mg_f(i["Scale"]))))
    fac = ((np.floor(ctx["u"] * s) + np.floor(ctx["v"] * s)) % 2).astype(np.float32)
    return {"Color": _mg_lerp(_mg_carr(i["Color B"], ctx["n"]), _mg_carr(i["Color A"], ctx["n"]), fac),
            "Fac": fac}


@material_node("Texture/Bricks", [("Scale", _F, 8.0), ("Mortar", _F, 0.06), ("Color", _C, (0.62, 0.27, 0.18, 1)),
                                  ("Color 2", _C, (0.48, 0.2, 0.14, 1)), ("Mortar Color", _C, (0.7, 0.68, 0.64, 1)),
                                  ("Seed", "int", 1)], [("Color", _C), ("Fac", _F)], "color",
               "Seamless running-bond bricks; Fac is 1 on bricks, 0 in the mortar (good for a height).")
def _mg_bricks_node(i, ctx):
    fac, var = _mg_bricks(ctx, _mg_f(i["Scale"]), _mg_f(i["Mortar"]), int(_mg_f(i["Seed"])))
    n = ctx["n"]
    brick = _mg_lerp(_mg_carr(i["Color"], n), _mg_carr(i["Color 2"], n), var)
    return {"Color": _mg_lerp(_mg_carr(i["Mortar Color"], n), brick, fac), "Fac": fac}


@material_node("Texture/Gradient", [("Angle", _F, 0.0)], [("Fac", _F)], "color",
               "A 0..1 ramp across the texture, turned by Angle (degrees).")
def _mg_grad(i, ctx):
    a = math.radians(_mg_f(i["Angle"]))
    t = (ctx["u"] - 0.5) * math.cos(a) + (ctx["v"] - 0.5) * math.sin(a)
    return {"Fac": np.clip(t + 0.5, 0, 1)}


@material_node("Color/Mix", [("Fac", _F, 0.5), ("A", _C, WHITE), ("B", _C, BLACK)], [("Color", _C)], "color")
def _mg_mix(i, ctx):
    return {"Color": _mg_lerp(_mg_c(i["A"]), _mg_c(i["B"]), np.clip(_mg_f(i["Fac"]), 0, 1)
                              if _mg_is_arr(_mg_f(i["Fac"])) else max(0.0, min(1.0, _mg_f(i["Fac"]))))}


@material_node("Color/Color Ramp", [("Fac", _F, 0.5), ("Color A", _C, BLACK), ("Color B", _C, WHITE),
                                    ("Position A", _F, 0.0), ("Position B", _F, 1.0)], [("Color", _C)], "color",
               "Maps a 0..1 value onto two colours (Position A/B set where the ramp starts and ends).")
def _mg_ramp(i, ctx):
    a, b = _mg_f(i["Position A"]), _mg_f(i["Position B"])
    t = (_mg_f(i["Fac"]) - a) / (b - a if abs(b - a) > 1e-6 else 1e-6)
    t = np.clip(t, 0, 1) if _mg_is_arr(t) else max(0.0, min(1.0, t))
    return {"Color": _mg_lerp(_mg_c(i["Color A"]), _mg_c(i["Color B"]), t)}


@material_node("Color/Brightness Contrast", [("Color", _C, WHITE), ("Bright", _F, 0.0), ("Contrast", _F, 0.0)],
               [("Color", _C)], "color")
def _mg_bc(i, ctx):
    c = _mg_c(i["Color"])
    c = np.asarray(c, dtype=np.float32) if isinstance(c, tuple) else c
    r = (c - 0.5) * (1.0 + _mg_f(i["Contrast"])) + 0.5 + _mg_f(i["Bright"])
    r = np.clip(r, 0, None)
    return {"Color": tuple(float(x) for x in r) if r.ndim == 1 else r}


@material_node("Color/Invert", [("Color", _C, WHITE)], [("Color", _C)], "color")
def _mg_inv(i, ctx):
    c = _mg_c(i["Color"])
    return {"Color": tuple(1.0 - x for x in c) if isinstance(c, tuple) else 1.0 - c}


@material_node("Color/Separate RGB", [("Color", _C, WHITE)], [("R", _F), ("G", _F), ("B", _F)], "color")
def _mg_sep(i, ctx):
    c = _mg_c(i["Color"])
    if isinstance(c, tuple):
        return {"R": c[0], "G": c[1], "B": c[2]}
    return {"R": c[..., 0], "G": c[..., 1], "B": c[..., 2]}


@material_node("Color/Combine RGB", [("R", _F, 0.0), ("G", _F, 0.0), ("B", _F, 0.0)], [("Color", _C)], "color")
def _mg_comb(i, ctx):
    r, g, b = _mg_f(i["R"]), _mg_f(i["G"]), _mg_f(i["B"])
    if not any(_mg_is_arr(x) for x in (r, g, b)):
        return {"Color": (r, g, b)}
    n = ctx["n"]
    return {"Color": np.stack([_mg_farr(r, n), _mg_farr(g, n), _mg_farr(b, n)], axis=2)}


for _nm, _fn in (("Add", lambda a, b: a + b), ("Subtract", lambda a, b: a - b), ("Multiply", lambda a, b: a * b),
                 ("Divide", lambda a, b: a / np.where(np.abs(b) < 1e-6, 1e-6, b) if _mg_is_arr(b) or _mg_is_arr(a)
                  else a / (b if abs(b) > 1e-6 else 1e-6)),
                 ("Power", lambda a, b: np.power(np.clip(a, 0, None), b) if _mg_is_arr(a) or _mg_is_arr(b)
                  else max(0.0, a) ** b),
                 ("Minimum", lambda a, b: np.minimum(a, b)), ("Maximum", lambda a, b: np.maximum(a, b))):
    material_node("Math/" + _nm, [("A", "any", 0.5), ("B", "any", 0.5)], [("Result", "any")], "math",
                  "%s - works on numbers and colours." % _nm)(_mg_math(_fn))


@material_node("Math/Map Range", [("Value", _F, 0.5), ("From Min", _F, 0.0), ("From Max", _F, 1.0),
                                  ("To Min", _F, 0.0), ("To Max", _F, 1.0)], [("Result", _F)], "math")
def _mg_map(i, ctx):
    a, b = _mg_f(i["From Min"]), _mg_f(i["From Max"])
    t = (_mg_f(i["Value"]) - a) / (b - a if abs(b - a) > 1e-6 else 1e-6)
    t = np.clip(t, 0, 1) if _mg_is_arr(t) else max(0.0, min(1.0, t))
    return {"Result": _mg_f(i["To Min"]) + (_mg_f(i["To Max"]) - _mg_f(i["To Min"])) * t}


@material_node("Math/Clamp", [("Value", _F, 0.5), ("Min", _F, 0.0), ("Max", _F, 1.0)], [("Result", _F)], "math")
def _mg_clamp(i, ctx):
    v = _mg_f(i["Value"])
    lo, hi = _mg_f(i["Min"]), _mg_f(i["Max"])
    return {"Result": np.clip(v, lo, hi) if _mg_is_arr(v) else max(lo, min(hi, v))}


@material_node("Math/One Minus", [("Value", _F, 0.5)], [("Result", _F)], "math")
def _mg_om(i, ctx):
    return {"Result": 1.0 - _mg_f(i["Value"])}


@material_node("Vector/Bump", [("Height", _F, 0.5), ("Strength", _F, 1.0)], [("Normal", "vector")], "vector",
               "Turns a height (noise, bricks Fac ...) into a normal map: light catches the bumps.")
def _mg_bump(i, ctx):
    h = _mg_f(i["Height"])
    if not _mg_is_arr(h):
        return {"Normal": None}
    return {"Normal": _mg_normal_from_height(h, _mg_f(i["Strength"]))}


@material_node("Vector/Normal Map", [("File", "string", ""), ("Tiling", _F, 1.0), ("Strength", _F, 1.0)],
               [("Normal", "vector")], "vector", "A tangent-space normal map image (OpenGL / glTF style).")
def _mg_nmap(i, ctx):
    img = _mg_image(i["File"], ctx.get("base_dir"), ctx["n"], _mg_f(i["Tiling"]))
    if img is None:
        return {"Normal": None}
    n = img[..., :3] * 2.0 - 1.0
    s = _mg_f(i["Strength"])
    n[..., 0] *= s
    n[..., 1] *= s
    ln = np.sqrt((n * n).sum(axis=2, keepdims=True)) + 1e-6
    return {"Normal": n / ln}


# ---- evaluation + baking -----------------------------------------------------------------
def material_graph_default(values=None):
    """A graph holding just a Material Output (optionally pre-filled)."""
    v = values or {}
    vals = {"Base Color": list(v.get("base_color", (0.8, 0.8, 0.8, 1.0)))[:4],
            "Metallic": float(v.get("metallic", 0.0)), "Roughness": float(v.get("roughness", 0.5)),
            "Emission": list(v.get("emission", (0, 0, 0)))[:3] + [1.0],
            "Emission Strength": float(v.get("emission_strength", 1.0) or 1.0), "Alpha": 1.0}
    if len(vals["Base Color"]) == 3:
        vals["Base Color"].append(1.0)
    return {"nodes": [{"id": 1, "type": "Output/Material Output", "pos": [420, 80], "values": vals}],
            "links": []}


def material_graph_eval(graph, n=MG_DEFAULT_RES, base_dir=None):
    """Evaluate a graph -> the Material Output's inputs (constants or arrays)."""
    nodes = {nd["id"]: nd for nd in graph.get("nodes", []) if nd.get("type") in MATERIAL_NODES}
    incoming = {}
    for a, an, b, bn in graph.get("links", []):
        if a in nodes and b in nodes:
            incoming[(b, bn)] = (a, an)
    k = (np.arange(n, dtype=np.float32) + 0.5) / n
    u, v = np.meshgrid(k, k)
    ctx = {"n": n, "u": u, "v": v, "base_dir": base_dir}
    memo, busy = {}, set()

    def inputs_of(nid):
        spec = MATERIAL_NODES[nodes[nid]["type"]]
        vals = nodes[nid].get("values", {})
        res = {}
        for name, typ, default in spec["inputs"]:
            src = incoming.get((nid, name))
            if src is not None:
                res[name] = run(src[0]).get(src[1])
            else:
                val = vals.get(name, default)
                res[name] = tuple(val) if isinstance(val, list) else val
        return res

    def run(nid):
        if nid in memo:
            return memo[nid]
        if nid in busy:
            return {}
        busy.add(nid)
        try:
            out = MATERIAL_NODES[nodes[nid]["type"]]["fn"](inputs_of(nid), ctx) or {}
        except Exception as ex:
            Log.warn(f"[Material] node {nodes[nid]['type']}: {ex}")
            out = {}
        busy.discard(nid)
        memo[nid] = out
        return out

    outs = [nid for nid, nd in nodes.items() if nd["type"] == "Output/Material Output"]
    if not outs:
        return material_graph_eval(material_graph_default(), n, base_dir)
    return inputs_of(outs[0])


def _mg_save_png(path, arr):
    a = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    if a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], axis=2)
    h, w = a.shape[:2]
    surf = pygame.image.frombuffer(np.ascontiguousarray(a).tobytes(), (w, h), "RGBA")
    tmp = path + ".tmp.png"
    pygame.image.save(surf, tmp)
    os.replace(tmp, path)


def material_vmat_read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _mg_graph_of(data):
    g = data.get("graph")
    if isinstance(g, dict) and g.get("nodes"):
        return g
    return material_graph_default(data)


def _mg_sources_sig(graph, base_dir):
    sig = []
    for nd in graph.get("nodes", []):
        f = (nd.get("values") or {}).get("File")
        if f:
            p = f if os.path.isabs(f) else os.path.join(base_dir, *str(f).replace("\\", "/").split("/"))
            try:
                sig.append((f, os.path.getmtime(p), os.path.getsize(p)))
            except OSError:
                sig.append((f, 0, 0))
    return sig


def material_asset_build(path, force=False, resolution=None):
    """Bake (when needed) the .vmat at ``path``.  Returns the material
    values: {"name", "base_color", "metallic", "roughness", "emissive",
    "emissive_intensity", "alpha_mode", "<slot>_tex": abs path or None,
    "baked": [abs paths], "rebaked": bool}."""
    path = os.path.abspath(path)
    data = material_vmat_read(path)
    graph = _mg_graph_of(data)
    base_dir = os.path.dirname(path)
    res = int(resolution or data.get("resolution") or MG_DEFAULT_RES)
    res = max(16, min(2048, res))
    stem = os.path.splitext(os.path.basename(path))[0]
    bdir = os.path.join(base_dir, ".baked")
    meta_p = os.path.join(bdir, stem + ".json")
    key = _mg_hashlib.sha1(json.dumps([graph, res, _mg_sources_sig(graph, base_dir), 2],
                                      sort_keys=True, default=str).encode()).hexdigest()
    if not force:
        meta = material_vmat_read(meta_p)
        if meta.get("key") == key:
            vals = dict(meta.get("values") or {})
            ok = True
            for slot in ("base_color_tex", "metallic_roughness_tex", "normal_tex", "emissive_tex"):
                if vals.get(slot):
                    vals[slot] = os.path.join(bdir, vals[slot])
                    ok = ok and os.path.isfile(vals[slot])
            if ok:
                vals["rebaked"] = False
                vals["name"] = stem
                return vals
    out = material_graph_eval(graph, res, base_dir)
    vals = {"name": stem, "base_color": [0.8, 0.8, 0.8, 1.0], "metallic": 0.0, "roughness": 0.5,
            "emissive": [0.0, 0.0, 0.0], "emissive_intensity": 1.0, "alpha_mode": "OPAQUE",
            "base_color_tex": None, "metallic_roughness_tex": None, "normal_tex": None, "emissive_tex": None}
    files = {}
    bc, al = out.get("Base Color"), out.get("Alpha", 1.0)
    bc = _mg_c(bc if bc is not None else (0.8, 0.8, 0.8))
    alf = _mg_f(al if al is not None else 1.0)
    if _mg_is_arr(bc) or _mg_is_arr(alf):
        rgba = np.concatenate([_mg_carr(bc, res), _mg_farr(alf, res)[..., None]], axis=2)
        files["base_color_tex"] = (stem + "_basecolor.png", rgba)
        vals["base_color"] = [1.0, 1.0, 1.0, 1.0]
        if _mg_is_arr(alf) and float(alf.min()) < 0.999:
            vals["alpha_mode"] = "BLEND"
    else:
        vals["base_color"] = [max(0.0, min(1.0, c)) for c in bc] + [max(0.0, min(1.0, alf))]
    if not _mg_is_arr(alf) and alf < 0.999:
        vals["alpha_mode"] = "BLEND"
    me = _mg_f(out.get("Metallic", 0.0) if out.get("Metallic") is not None else 0.0)
    ro = _mg_f(out.get("Roughness", 0.5) if out.get("Roughness") is not None else 0.5)
    if _mg_is_arr(me) or _mg_is_arr(ro):
        mr = np.stack([np.ones((res, res), np.float32), _mg_farr(ro, res), _mg_farr(me, res)], axis=2)
        files["metallic_roughness_tex"] = (stem + "_metalrough.png", np.clip(mr, 0, 1))
        vals["metallic"], vals["roughness"] = 1.0, 1.0
    else:
        vals["metallic"], vals["roughness"] = max(0.0, min(1.0, me)), max(0.04, min(1.0, ro))
    nm = out.get("Normal")
    if _mg_is_arr(nm) and nm.ndim == 3:
        files["normal_tex"] = (stem + "_normal.png", nm * 0.5 + 0.5)
    em = _mg_c(out.get("Emission") if out.get("Emission") is not None else (0, 0, 0))
    es = _mg_f(out.get("Emission Strength", 1.0) if out.get("Emission Strength") is not None else 1.0)
    if _mg_is_arr(em) or _mg_is_arr(es):
        e = _mg_carr(em, res) * (_mg_farr(es, res)[..., None] if _mg_is_arr(es) else 1.0)
        files["emissive_tex"] = (stem + "_emission.png", np.clip(e, 0, 1))
        vals["emissive"] = [1.0, 1.0, 1.0]
        vals["emissive_intensity"] = float(es) if not _mg_is_arr(es) else 1.0
    else:
        vals["emissive"] = [max(0.0, c) for c in em]
        vals["emissive_intensity"] = max(0.0, es)
    os.makedirs(bdir, exist_ok=True)
    stored = dict(vals)
    for slot, (fname, arr) in files.items():
        _mg_save_png(os.path.join(bdir, fname), arr)
        stored[slot] = fname
        vals[slot] = os.path.join(bdir, fname)
    for slot in ("base_color_tex", "metallic_roughness_tex", "normal_tex", "emissive_tex"):
        if slot not in files:                         # drop stale bakes of now-constant slots
            old = os.path.join(bdir, "%s_%s.png" % (stem, {"base_color_tex": "basecolor",
                                                            "metallic_roughness_tex": "metalrough",
                                                            "normal_tex": "normal",
                                                            "emissive_tex": "emission"}[slot]))
            try:
                os.remove(old)
            except OSError:
                pass
    with open(meta_p + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"key": key, "values": stored}, f)
    os.replace(meta_p + ".tmp", meta_p)
    vals["rebaked"] = True
    vals["baked"] = [vals[s] for s in files]
    return vals


def material_vmat_write(path, graph, resolution=None):
    """Save a graph into a .vmat, keeping the summary values the asset
    browser shows (colour ball) in step with the bake."""
    data = material_vmat_read(path)
    data["type"] = "material"
    data["graph"] = {"nodes": graph.get("nodes", []), "links": graph.get("links", [])}
    if resolution:
        data["resolution"] = int(resolution)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    vals = material_asset_build(path)
    bc = vals.get("base_color", [0.8, 0.8, 0.8, 1])
    if vals.get("base_color_tex"):
        try:
            s = pygame.image.load(vals["base_color_tex"])
            avg = pygame.transform.average_color(s)
            bc = [avg[0] / 255.0, avg[1] / 255.0, avg[2] / 255.0, 1.0]
        except Exception:
            pass
    data.update({"base_color": list(bc), "metallic": vals.get("metallic", 0.0),
                 "roughness": vals.get("roughness", 0.5), "emission": list(vals.get("emissive", [0, 0, 0])),
                 "emission_strength": vals.get("emissive_intensity", 1.0)})
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    return vals


def material_asset_create(path, values=None):
    """A new .vmat with a starter graph."""
    path = os.path.abspath(path)
    if not path.lower().endswith(MATERIAL_EXT):
        path += MATERIAL_EXT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "material", "base_color": [0.8, 0.8, 0.8, 1.0], "roughness": 0.5, "metallic": 0.0,
                   "emission": [0, 0, 0], "emission_strength": 0.0}, f)
    material_vmat_write(path, material_graph_default(values))
    return path


# ---- objects using a material asset -------------------------------------------------------
def _mg_targets(node):
    out = [node]
    for c in node.children:
        if getattr(c, "_heaven3d_imported_part", False):
            out.append(c)
    return [n for n in out if n.mesh is not None or n is node]


def _mg_material_from(vals):
    m = Material(name=vals.get("name", "material"),
                 base_color=tuple(vals.get("base_color", (0.8, 0.8, 0.8, 1.0))),
                 metallic=float(vals.get("metallic", 0.0)), roughness=float(vals.get("roughness", 0.5)),
                 emissive=tuple(vals.get("emissive", (0, 0, 0))),
                 emissive_intensity=float(vals.get("emissive_intensity", 1.0)),
                 alpha_mode=vals.get("alpha_mode", "OPAQUE"))
    for slot in ("base_color_tex", "metallic_roughness_tex", "normal_tex", "emissive_tex"):
        if vals.get(slot):
            setattr(m, slot, ("path", vals[slot]))
    return m


def _mg_evict(engine, vals):
    r = getattr(engine, "renderer", None)
    cache = getattr(r, "_tex_cache", None)
    if not isinstance(cache, dict):
        return
    for slot in ("base_color_tex", "metallic_roughness_tex", "normal_tex", "emissive_tex"):
        p = vals.get(slot)
        if p:
            t = cache.pop(os.path.abspath(p), None)
            if t is not None and t is not getattr(r, "_default_tex", None):
                try:
                    t.release()
                except Exception:
                    pass


def _mg_apply(engine, node, path, vals):
    for t in _mg_targets(node):
        t.material = _mg_material_from(vals)
    node.material_asset = os.path.normpath(os.path.abspath(path))


def _mg_assign(self, node, path):
    """Give ``node`` (and the parts of an imported model) the material
    asset at ``path`` (absolute or project-relative).  Returns the values."""
    if node is None:
        raise ValueError("no object")
    if node.is_sun or node.is_clouds or node.is_ocean:
        raise ValueError(f"the {node.name} has no material")
    if not os.path.isabs(path):
        pd = os.path.dirname(os.path.abspath(self.project_path)) if getattr(self, "project_path", None) \
            else os.getcwd()
        path = os.path.join(pd, *str(path).replace("\\", "/").split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"material not found: {path}")
    vals = material_asset_build(path)
    if vals.get("rebaked"):
        _mg_evict(self, vals)
    _mg_apply(self, node, path, vals)
    return vals


def _mg_users(self, path=None):
    want = os.path.normcase(os.path.abspath(path)) if path else None
    return [n for n in self.scene.root.traverse()
            if getattr(n, "material_asset", None)
            and (want is None or os.path.normcase(n.material_asset) == want)]


def _mg_refresh(self, path, force=False):
    """Re-bake a .vmat and update every object using it.  Returns how many."""
    vals = material_asset_build(path, force=force)
    _mg_evict(self, vals)
    users = self.material_asset_users(path)
    for n in users:
        _mg_apply(self, n, path, vals)
    return len(users)


def _mg_detach(self, node):
    """Keep the look, forget the asset (the object's material becomes its own)."""
    if getattr(node, "material_asset", None):
        node.material_asset = None


HeavenEngine.assign_material_asset = _mg_assign
HeavenEngine.material_asset_users = _mg_users
HeavenEngine.refresh_material_asset = _mg_refresh
HeavenEngine.detach_material_asset = _mg_detach


# ---- projects / prefabs remember the asset ------------------------------------------------
_MG_PREV_SER = ProjectSerializer._serialize_node.__func__
_MG_PREV_DESER = ProjectSerializer._deserialize_node.__func__


def _mg_serialize_node(cls, node, project_dir):
    d = _MG_PREV_SER(cls, node, project_dir)
    p = getattr(node, "material_asset", None)
    if p:
        try:
            d["material_asset"] = _to_portable_path(p, project_dir)
        except Exception:
            d["material_asset"] = p
    return d


def _mg_deserialize_node(cls, data, parent, engine, project_dir):
    before = len(parent.children)
    _MG_PREV_DESER(cls, data, parent, engine, project_dir)
    raw = data.get("material_asset") if isinstance(data, dict) else None
    if not raw or len(parent.children) <= before:
        return
    node = parent.children[-1]
    try:
        p = _from_portable_path(raw, project_dir)
    except Exception:
        p = raw
    if (not p or not os.path.isfile(p)) and not os.path.isabs(str(raw)):
        p = os.path.join(project_dir, *str(raw).replace("\\", "/").split("/"))
    if not os.path.isfile(p):
        node.material_asset = os.path.normpath(p)       # keep the saved look + the link
        return
    try:
        engine.assign_material_asset(node, p)
    except Exception as ex:
        node.material_asset = os.path.normpath(p)
        Log.warn(f"[Material] {os.path.basename(p)}: {ex} (kept the saved material)")


ProjectSerializer._serialize_node = classmethod(_mg_serialize_node)
ProjectSerializer._deserialize_node = classmethod(_mg_deserialize_node)

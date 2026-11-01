"""Builds the game-ready vegetation, rocks and terrain textures of the
Tropical Islands package from CC0 sources (needs a GPU / Xvfb for baking):

    python tools/build_assets.py SRC_DIR PALM_DIR OUT_ASSETS_DIR [names...]

Sources (all CC0 1.0):
  Poly Haven (polyhaven.com): island_tree_01, jacaranda_tree, tree_small_02,
      grass_medium_02, grass_medium_01, flower_gazania, periwinkle_plant,
      boulder_01, rock_moss_set_01 (glTF 1k / 2k)  and the textures
      aerial_beach_01, forrest_ground_01, dirt_floor, mossy_rock (2k)
  Nobiax / yughues (opengameart.org): "Free palm treeZ v3" (PACK V3) and
      "Palm tree v2"

What it does:
  * trees: leaves + twigs are clustered and every cluster is rendered on the
    GPU into a tile of a texture atlas (colour + alpha + normal + baked canopy
    AO), then replaced by one card (billboard quad).  3 LODs from the same
    atlas (many small cards -> few big ones).  Trunks / big branches are
    decimated per UV chart (textures stay correct).
  * grass / flowers: every clump variant is baked into 3 crossed cards.
  * rocks: decimated per UV chart, 3 LODs.
  * palms: already game-ready cards; converted, scaled to real heights.
Output: assets/veg/<name>.npz (+ textures), assets/terrain/*.jpg
"""
import json
import math
import os
import shutil
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gltf_lite import GLTF  # noqa: E402

# --------------------------------------------------------------------------- catalogue
TREES = {
    # name: (gltf, target height m (None = keep), cards per LOD, atlas tile px)
    "island_tree": ("island_tree_01", 7.5, (150, 44, 16), 96),
    "jacaranda": ("jacaranda_tree", 12.0, (150, 44, 16), 96),
    "acacia": ("tree_small_02", 7.0, (150, 44, 16), 96),
}
CLUMPS = {
    # name: (gltf, max variants, tile px, kind)
    "grass_tall": ("grass_medium_02", 5, 256, "grass"),
    "grass_lush": ("grass_medium_01", 6, 256, "grass"),
    "flower_orange": ("flower_gazania", 8, 256, "flower"),
    "flower_pink": ("periwinkle_plant", 6, 256, "flower"),
}
ROCKS = {
    "boulder": ("boulder_01", (3500, 900, 220)),
    "mossy_rocks": ("rock_moss_set_01", (1600, 420, 110)),
}
PALMS = {
    # name: (pack dir, [obj files], diffuse, normal, specular, height range m)
    "coconut_palm": ("v3", ["palm_straight", "palm_bend", "palm_dual", "palm_trio", "palm_dual_bend"],
                     "diffuse.tga", "normal.tga", "specular.tga", (8.5, 13.0)),
    "royal_palm": ("v2", ["palm_tree"], "diffus.tga", "normal.tga", "specular.tga", (11.0, 16.0)),
}
# runtime albedo tints (engine.py TINTS) - impostors are baked with them so they match
CARD_TINT = (1.55, 1.65, 1.25)
WOOD_TINT = {"island_tree": (1.0, 0.96, 0.9)}
PALM_TINT = (1.3, 1.35, 1.1)
TERRAIN = ["aerial_beach_01", "forrest_ground_01", "dirt_floor", "mossy_rock"]


# --------------------------------------------------------------------------- GL baking
class Baker:
    VS = """
    #version 330
    in vec3 in_p; in vec3 in_n; in vec2 in_uv; in float in_ao;
    uniform vec3 c; uniform vec3 u; uniform vec3 v; uniform vec3 n; uniform vec3 ext;
    out vec3 wn; out vec2 uv; out float ao;
    void main(){
        vec3 d = in_p - c;
        gl_Position = vec4(dot(d,u)/ext.x, dot(d,v)/ext.y, -dot(d,n)/ext.z, 1.0);
        wn = in_n; uv = in_uv; ao = in_ao;
    }"""
    FS = """
    #version 330
    in vec3 wn; in vec2 uv; in float ao;
    uniform sampler2D tex; uniform vec3 u; uniform vec3 v; uniform vec3 n; uniform vec3 tint; uniform int objspace;
    layout(location=0) out vec4 col; layout(location=1) out vec4 nor;
    void main(){
        vec3 N = normalize(wn);
        if (objspace == 0 && dot(N, n) < 0.0) N = -N;      // two-sided leaves face the card
        vec3 t = vec3(dot(N,u), dot(N,v), dot(N,n));
        t.z = max(t.z, 0.25); t = normalize(t);
        vec4 tx = texture(tex, uv);
        if (tx.a < 0.5) discard;
        vec3 a = tx.rgb * tint * ao;
        col = vec4(a, 1.0);
        nor = objspace == 1 ? vec4(N * 0.5 + 0.5, 1.0) : vec4(t * 0.5 + 0.5, 1.0);
    }"""

    def __init__(self, size):
        import moderngl
        self.mgl = moderngl
        self.ctx = moderngl.create_standalone_context()
        self.size = size
        self.prog = self.ctx.program(vertex_shader=self.VS, fragment_shader=self.FS)
        self.prog["objspace"].value = 0
        self.col = self.ctx.texture((size, size), 4)
        self.nor = self.ctx.texture((size, size), 4)
        self.dep = self.ctx.depth_renderbuffer((size, size))
        self.fbo = self.ctx.framebuffer([self.col, self.nor], self.dep)
        self.fbo.use()
        self.ctx.clear(0, 0, 0, 0)
        self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.textures = {}

    def texture(self, path):
        t = self.textures.get(path)
        if t is None:
            im = Image.open(path).convert("RGBA")
            t = self.ctx.texture(im.size, 4, im.tobytes())
            t.build_mipmaps()
            t.filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR)
            self.textures[path] = t
        return t

    def upload(self, P, N, UV, AO, I):
        """GPU buffers of a geometry set; returns a handle for draw()."""
        data = np.hstack([P, N, UV, AO[:, None]]).astype("f4")
        vbo = self.ctx.buffer(data.tobytes())
        ibo = self.ctx.buffer(I.astype("u4").tobytes())
        vao = self.ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f 1f", "in_p", "in_n", "in_uv", "in_ao")], ibo)
        return vao

    def draw(self, tile_rect, frame, items):
        """tile_rect = (x, y, w, h) px; frame = (c, u, v, n, ext); items = [(vao, tex, first_tri, n_tri, tint)]"""
        x, y, w, h = tile_rect
        self.fbo.use()
        self.ctx.viewport = (x, y, w, h)
        self.ctx.scissor = (x, y, w, h)
        self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        c, u, v, n, ext = frame
        for k, val in (("c", c), ("u", u), ("v", v), ("n", n), ("ext", ext)):
            self.prog[k].value = tuple(float(a) for a in val)
        for vao, tex, first, cnt, tint in items:
            if cnt <= 0:
                continue
            self.texture(tex).use(0)
            self.prog["tex"].value = 0
            self.prog["tint"].value = tuple(tint)
            vao.render(self.mgl.TRIANGLES, vertices=cnt * 3, first=first * 3)
        self.ctx.scissor = None

    def read(self):
        c = np.frombuffer(self.col.read(), np.uint8).reshape(self.size, self.size, 4)
        n = np.frombuffer(self.nor.read(), np.uint8).reshape(self.size, self.size, 4)
        return c.copy(), n.copy()


def dilate(rgba, nor):
    """Fill transparent texels with the nearest opaque colour (no dark mip fringes)."""
    from scipy.ndimage import distance_transform_edt
    a = rgba[..., 3] > 0
    if not a.any():
        return rgba, nor
    _, (iy, ix) = distance_transform_edt(~a, return_indices=True)
    rgb = rgba[..., :3][iy, ix]
    nn = nor[..., :3][iy, ix]
    out = np.dstack([rgb, rgba[..., 3]])
    nout = np.dstack([nn, np.full(a.shape, 255, np.uint8)])
    return out, nout


def save_atlas(rgba, nor, out_base, flip=True):
    """Atlas row 0 is the bottom (GL); images are stored top-down like glTF."""
    from scipy.ndimage import maximum_filter
    rgba, nor = dilate(rgba, nor)                         # colour-fill first, so thickened edges get leaf colour
    a = rgba[..., 3]
    rgba = rgba.copy()
    rgba[..., 3] = np.maximum(a, (maximum_filter(a, size=3).astype(np.float32) * 0.75).astype(np.uint8))
    if flip:
        rgba, nor = rgba[::-1], nor[::-1]
    Image.fromarray(rgba, "RGBA").save(out_base + "_albedo.png", optimize=True)
    Image.fromarray(nor[..., :3], "RGB").save(out_base + "_normal.jpg", quality=90)


# --------------------------------------------------------------------------- geometry helpers
def components(I, n):
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components
    A = sp.coo_matrix((np.ones(I.size), (I[:, [0, 1, 2]].reshape(-1), I[:, [1, 2, 0]].reshape(-1))), shape=(n, n))
    return connected_components(A, directed=False)[1]


def decimate(P, N, UV, I, target):
    """Quadric decimation per UV chart; UVs / normals re-sampled from the nearest
    original vertex of the same chart."""
    import fast_simplification as fs
    from scipy.spatial import cKDTree
    if len(I) <= target:
        return P, N, UV, I
    lab = components(I, len(P))
    flab = lab[I[:, 0]]
    ratio = target / len(I)
    outP, outN, outU, outI = [], [], [], []
    base = 0
    for c in np.unique(flab):
        F = I[flab == c]
        vid = np.unique(F)
        remap = np.full(len(P), -1, np.int64)
        remap[vid] = np.arange(len(vid))
        lp, lf = P[vid], remap[F]
        want = max(4, int(len(lf) * ratio))
        if len(lf) > want + 4:
            try:
                np_, nf = fs.simplify(lp.astype(np.float32), lf.astype(np.int32), target_reduction=1.0 - want / len(lf))
            except Exception:
                np_, nf = lp, lf
        else:
            np_, nf = lp, lf
        if len(nf) == 0:
            continue
        tree = cKDTree(lp)
        _, nearest = tree.query(np_)
        outP.append(np.asarray(np_, np.float64))
        outN.append(N[vid][nearest])
        outU.append(UV[vid][nearest])
        outI.append(np.asarray(nf, np.int64) + base)
        base += len(np_)
    P2, N2, U2, I2 = np.vstack(outP), np.vstack(outN), np.vstack(outU), np.vstack(outI)
    # smooth normals from the decimated geometry, blended with the sampled ones
    fn = np.cross(P2[I2[:, 1]] - P2[I2[:, 0]], P2[I2[:, 2]] - P2[I2[:, 0]])
    vn = np.zeros_like(P2)
    for k in range(3):
        np.add.at(vn, I2[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    N2 = vn * 0.5 + N2 * 0.5
    N2 /= np.maximum(np.linalg.norm(N2, axis=1, keepdims=True), 1e-12)
    return P2, N2, U2, I2


def decimate_welded(P, N, UV, I, target):
    """Decimates the position-welded mesh (UV seams don't block reduction), then
    gives every new triangle the UV chart of the original triangle under its
    centre and each corner the UV of the nearest vertex of that chart.
    Output is unindexed per-triangle corners (fine for small meshes)."""
    import fast_simplification as fs
    from scipy.spatial import cKDTree
    if len(I) <= target:
        return P, N, UV, I
    key = np.round(P / 1e-5).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    WP = np.zeros((inv.max() + 1, 3))
    WP[inv] = P
    WI = inv[I]
    WI = WI[(WI[:, 0] != WI[:, 1]) & (WI[:, 1] != WI[:, 2]) & (WI[:, 0] != WI[:, 2])]
    np_, nf = fs.simplify(WP.astype(np.float32), WI.astype(np.int32), target_reduction=1.0 - target / len(WI))
    np_ = np.asarray(np_, np.float64)
    nf = np.asarray(nf, np.int64)
    chart = components(I, len(P))
    fchart = chart[I[:, 0]]
    fcen = P[I].mean(1)
    ftree = cKDTree(fcen)
    _, near_f = ftree.query(np_[nf].mean(1))
    tri_chart = fchart[near_f]
    out_P = np_[nf].reshape(-1, 3)
    out_UV = np.zeros((len(out_P), 2))
    out_N = np.zeros((len(out_P), 3))
    corner_chart = np.repeat(tri_chart, 3)
    for c in np.unique(corner_chart):
        sel = corner_chart == c
        vid = np.unique(I[fchart == c])
        tr = cKDTree(P[vid])
        _, nn = tr.query(out_P[sel])
        out_UV[sel] = UV[vid][nn]
        out_N[sel] = N[vid][nn]
    # smooth shading from the decimated surface, blended with the original normals
    fn = np.cross(np_[nf[:, 1]] - np_[nf[:, 0]], np_[nf[:, 2]] - np_[nf[:, 0]])
    vn = np.zeros_like(np_)
    for k in range(3):
        np.add.at(vn, nf[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    sm = vn[nf].reshape(-1, 3)
    out_N = sm * 0.6 + out_N * 0.4
    out_N /= np.maximum(np.linalg.norm(out_N, axis=1, keepdims=True), 1e-12)
    return out_P, out_N, out_UV, np.arange(len(out_P)).reshape(-1, 3)


def frame_for(points, outward=None, up=np.array([0.0, 1.0, 0.0]), pad=1.06):
    """Card frame (c, u, v, n, ext) fitting points in their best plane."""
    c0 = points.mean(0)
    d = points - c0
    w, V = np.linalg.eigh(d.T @ d / max(1, len(d)))
    n = V[:, 0]
    if outward is not None:
        if np.dot(n, outward) < 0:
            n = -n
        n = n + 0.9 * outward
        n /= np.linalg.norm(n)
    u = np.cross(up, n)
    if np.linalg.norm(u) < 0.3:
        u = V[:, 2] - n * np.dot(V[:, 2], n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    pu, pv, pn = d @ u, d @ v, d @ n
    cu, cv = (pu.max() + pu.min()) / 2, (pv.max() + pv.min()) / 2
    c = c0 + u * cu + v * cv
    eu = max(1e-3, (pu.max() - pu.min()) / 2 * pad)
    ev = max(1e-3, (pv.max() - pv.min()) / 2 * pad)
    ez = max(1e-3, np.abs(pn).max() * 1.5 + 0.05)
    return c, u, v, n, np.array([eu, ev, ez])


def frame_with_normal(points, n, up=np.array([0.0, 1.0, 0.0]), pad=1.06):
    n = n / np.linalg.norm(n)
    c0 = points.mean(0)
    d = points - c0
    u = np.cross(up, n)
    if np.linalg.norm(u) < 0.3:
        u = np.cross([1.0, 0.0, 0.0], n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    pu, pv, pn = d @ u, d @ v, d @ n
    c = c0 + u * (pu.max() + pu.min()) / 2 + v * (pv.max() + pv.min()) / 2
    ext = np.array([max(1e-3, (pu.max() - pu.min()) / 2 * pad), max(1e-3, (pv.max() - pv.min()) / 2 * pad),
                    max(1e-3, np.abs(pn).max() * 1.5 + 0.05)])
    return c, u, v, n, ext


def quad(frame, uv_rect, sway0=0.0, sway1=1.0):
    """Card geometry: 4 verts (P, N, UV, sway) + 2 tris.  uv_rect = (u0, v0, u1, v1) top-down."""
    c, u, v, n, ext = frame
    eu, ev = ext[0], ext[1]
    P = np.array([c - u * eu - v * ev, c + u * eu - v * ev, c + u * eu + v * ev, c - u * eu + v * ev])
    u0, v0, u1, v1 = uv_rect
    UV = np.array([[u0, v1], [u1, v1], [u1, v0], [u0, v0]])
    N = np.tile(n, (4, 1))
    I = np.array([[0, 1, 2], [0, 2, 3]])
    return P, N, UV, I


class Packer:
    """Fixed-size tile grid in an atlas.  Tile (col,row) row 0 = bottom (GL)."""

    def __init__(self, size, tile):
        self.size, self.tile, self.k = size, tile, 0
        self.cols = size // tile

    def next(self):
        col, row = self.k % self.cols, self.k // self.cols
        self.k += 1
        if row >= self.cols:
            raise RuntimeError("atlas full")
        x, y = col * self.tile, row * self.tile
        t = self.tile
        s = float(self.size)
        inset = 1.0
        # top-down UVs (image flipped when saved): v = 1 - y/size
        uvr = ((x + inset) / s, 1.0 - (y + t - inset) / s, (x + t - inset) / s, 1.0 - (y + inset) / s)
        return (x, y, t, t), uvr


# --------------------------------------------------------------------------- builders
def _merge(parts):
    P, N, UV, I, S = [], [], [], [], []
    base = 0
    for p, n, uv, i, s in parts:
        P.append(p); N.append(n); UV.append(uv); I.append(i + base); S.append(s)
        base += len(p)
    if not P:
        return None
    P, N, UV, I, S = np.vstack(P), np.vstack(N), np.vstack(UV), np.vstack(I), np.concatenate(S)
    # weld identical corners (decimation / OBJ output is unindexed)
    key = np.hstack([np.round(P / 1e-5), np.round(N * 1e3), np.round(UV / 1e-5)]).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    return {"P": P[first].astype("f4"), "N": N[first].astype("f4"), "UV": UV[first].astype("f4"),
            "I": inv[I].astype("u4"), "S": S[first].astype("f4")}


def bake_impostors(name, variants_parts, out_dir, tile=512, size=2048, top=True):
    """Bakes every variant from 2 sides into an atlas; returns per-variant crossed-quad meshes.
    variants_parts: [[(P, N, UV, I, tex), ...], ...] (LOD0 geometry, base at the origin)."""
    baker = Baker(size)
    pk = Packer(size, tile)
    out = []
    for parts in variants_parts:
        allp = np.vstack([p[0] for p in parts])
        H = float(allp[:, 1].max())
        quads = []
        vaos = [(baker.upload(p[0], p[1], p[2], np.ones(len(p[0])), p[3]), p[4], len(p[3]),
                 tuple(p[5]) if len(p) > 5 else (1.0, 1.0, 1.0)) for p in parts]
        for k in range(2):
            a = math.pi * 0.5 * k + 0.35
            n = np.array([math.sin(a), 0.0, math.cos(a)])
            u = np.cross([0.0, 1.0, 0.0], n); u /= np.linalg.norm(u)
            v = np.array([0.0, 1.0, 0.0])
            pu = allp @ u
            R = float(max(abs(pu.min()), abs(pu.max()), 0.1)) * 1.02
            pn = allp @ n
            c = np.array([0.0, H / 2, 0.0])
            fr = (c, u, v, n, np.array([R, H / 2 * 1.02, max(abs(pn).max(), 0.1) * 1.5 + 0.1]))
            rect, uvr = pk.next()
            baker.draw(rect, fr, [(vao, tex, 0, cnt, tn) for vao, tex, cnt, tn in vaos])
            qp, qn, quv, qi = quad(fr, uvr)
            qn = qn * 0.45 + np.array([0.0, 1.0, 0.0]) * 0.55
            qn /= np.linalg.norm(qn, axis=1, keepdims=True)
            quads.append((qp, qn, quv, qi, np.clip(qp[:, 1] / H, 0, 1) ** 1.5))
        # top-down card so canopies read from the air
        if top:
            n = np.array([0.0, 1.0, 0.0])
            u = np.array([1.0, 0.0, 0.0])
            v = np.array([0.0, 0.0, -1.0])
            R = float(np.abs(allp[:, [0, 2]]).max()) * 1.02
            ytop = float(np.percentile(allp[:, 1], 75))
            c = np.array([0.0, ytop, 0.0])
            fr = (c, u, v, n, np.array([R, R, H * 1.5]))
            rect, uvr = pk.next()
            baker.draw(rect, fr, [(vao, tex, 0, cnt, tn) for vao, tex, cnt, tn in vaos])
            qp, qn, quv, qi = quad(fr, uvr)
            quads.append((qp, qn, quv, qi, np.full(4, 0.8)))
        for vao, _t, _c, _n in vaos:
            vao.release()
        out.append(_merge(quads))
    rgba, nor = baker.read()
    save_atlas(rgba, nor, os.path.join(out_dir, name + "_imp"))
    return out


OCT_N = 8                                   # 8 x 8 hemi-octahedral views per impostor


def oct_dir(i, j, n=OCT_N):
    """Frame (i, j) of a hemi-octahedral grid -> unit view direction (y up).
    Inverse of the runtime mapping g = (px + pz, px - pz), px/pz = v.xz / |v|_1."""
    gx, gy = i / (n - 1) * 2.0 - 1.0, j / (n - 1) * 2.0 - 1.0
    px, pz = (gx + gy) * 0.5, (gx - gy) * 0.5
    y = max(0.0, 1.0 - abs(px) - abs(pz))
    d = np.array([px, y, pz])
    return d / max(np.linalg.norm(d), 1e-9)


def oct_basis(d):
    up = np.array([0.0, 1.0, 0.0])
    r = np.cross(up, d)
    ln = np.linalg.norm(r)
    r = r / ln if ln > 1e-3 else np.array([1.0, 0.0, 0.0])
    return r, np.cross(d, r)


def bake_octahedral(name, variants_parts, out_dir, size=1024, n=OCT_N):
    """Amplify-style hemi-octahedral impostors: n x n orthographic views over the
    upper hemisphere per variant (colour + alpha, object-space normals), all at the
    same scale (bounding sphere radius R around the centre (0, cy, 0))."""
    tile = size // n
    metas = []
    for vi, parts in enumerate(variants_parts):
        baker = Baker(size)
        baker.prog["objspace"].value = 1
        allp = np.vstack([p[0] for p in parts])
        cy = float((allp[:, 1].max() + allp[:, 1].min()) * 0.5)
        c = np.array([0.0, cy, 0.0])
        R = float(np.linalg.norm(allp - c, axis=1).max()) * 1.01
        vaos = [(baker.upload(p[0], p[1], p[2], np.ones(len(p[0])), p[3]), p[4], len(p[3]),
                 tuple(p[5]) if len(p) > 5 else (1.0, 1.0, 1.0)) for p in parts]
        for j in range(n):
            for i in range(n):
                d = oct_dir(i, j, n)
                r, u = oct_basis(d)
                fr = (c, r, u, d, np.array([R, R, R * 1.05]))
                baker.draw((i * tile, j * tile, tile, tile), fr, [(vao, tex, 0, cnt, tn) for vao, tex, cnt, tn in vaos])
        for vao, _t, _c, _n in vaos:
            vao.release()
        rgba, nor = baker.read()
        base = os.path.join(out_dir, f"{name}_oct{vi}")
        save_atlas(rgba, nor, base)
        metas.append({"albedo": f"{name}_oct{vi}_albedo.png", "normal": f"{name}_oct{vi}_normal.jpg",
                      "radius": R, "cy": cy})
    return {"n": n, "variants": metas}


def build_tree(name, src_dir, out_dir, baker):
    gname, height, ncards, tile = TREES[name]
    g = GLTF(os.path.join(src_dir, gname, gname + ".gltf"))
    wood, foliage = [], []         # wood: (P,N,UV,I,mat) ; foliage: (P,N,UV,I,tex)
    for nm, mi, P, N, UV, I in g.primitives():
        m = g.material(mi)
        mn = m["name"].lower()
        if "leaves" in mn or "leaf" in mn:
            foliage.append((P, N, UV, I, m))
        elif "branch" in mn:
            lab = components(I, len(P))
            # big branch pieces stay wood, twigs go to the cards
            ext = np.zeros(lab.max() + 1)
            for k in range(3):
                mx = np.full(lab.max() + 1, -1e9); mn_ = np.full(lab.max() + 1, 1e9)
                np.maximum.at(mx, lab, P[:, k]); np.minimum.at(mn_, lab, P[:, k])
                ext = np.maximum(ext, mx - mn_)
            hh = P[:, 1].max() - P[:, 1].min()
            big = ext[lab[I[:, 0]]] > max(0.9, 0.12 * hh)
            if big.any():
                wood.append((P, N, UV, I[big], m))
            if (~big).any():
                foliage.append((P, N, UV, I[~big], m))
        else:
            wood.append((P, N, UV, I, m))
    allP = np.vstack([w[0][np.unique(w[3])] for w in wood] + [f[0][np.unique(f[3])] for f in foliage])
    y0 = allP[:, 1].min()
    h = allP[:, 1].max() - y0
    s = (height / h) if height else 1.0
    cx, cz = np.median(np.vstack([w[0] for w in wood])[:, [0, 2]], axis=0)
    off = np.array([cx, y0, cz])

    def tr(P):
        return (P - off) * s
    H = h * s
    # canopy centre / radius for AO and card orientation
    fol_pts = np.vstack([tr(f[0][np.unique(f[3])]) for f in foliage])
    cc = np.array([0.0, np.median(fol_pts[:, 1]), 0.0])
    rr = np.maximum(np.linalg.norm((fol_pts - cc) * [1, 1.3, 1], axis=1), 1e-3)
    rmax = np.percentile(rr, 95)
    # upload foliage, faces sorted later per clustering
    comp_cent, comp_area, faces_of = [], [], []
    up_items = []
    for fi, (P, N, UV, I, m) in enumerate(foliage):
        Pt = tr(P)
        r = np.linalg.norm((Pt - cc) * [1, 1.3, 1], axis=1)
        AO = np.clip(0.45 + 0.55 * (r / rmax) ** 1.5, 0.45, 1.0)
        AO *= np.clip(0.75 + 0.25 * Pt[:, 1] / H, 0.6, 1.0)
        lab = components(I, len(P))
        flab = lab[I[:, 0]]
        nlab = lab.max() + 1
        cen = np.zeros((nlab, 3)); cnt = np.zeros(nlab)
        np.add.at(cen, flab, Pt[I[:, 0]]); np.add.at(cnt, flab, 1)
        ok = cnt > 0
        cen[ok] /= cnt[ok][:, None]
        up_items.append({"P": Pt, "N": N, "UV": UV, "AO": AO, "I": I, "flab": flab, "cen": cen, "ok": ok,
                         "tex": m["base"]})
    tint = (1.18, 1.22, 1.05)                            # lush tropical foliage
    lods = []
    pk = Packer(baker.size, tile)
    for li, K in enumerate(ncards):
        # cluster all foliage components together
        cents = np.vstack([it["cen"][it["ok"]] for it in up_items])
        from scipy.cluster.vq import kmeans2
        rng = np.random.default_rng(7 + li)
        K = min(K, len(cents))
        _, lab_c = kmeans2(cents * [1, 1.15, 1], K, minit="++", seed=rng)
        # per item: component -> cluster
        start = 0
        per_item = []
        for it in up_items:
            ids = np.where(it["ok"])[0]
            comp2cl = np.full(len(it["ok"]), -1)
            comp2cl[ids] = lab_c[start:start + len(ids)]
            start += len(ids)
            fcl = comp2cl[it["flab"]]
            order = np.argsort(fcl, kind="stable")
            I_sorted = it["I"][order]
            fcl_sorted = fcl[order]
            vao = baker.upload(it["P"], it["N"], it["UV"], it["AO"], I_sorted)
            bounds = np.searchsorted(fcl_sorted, np.arange(K + 1))
            per_item.append((it, vao, I_sorted, bounds))
        parts = []
        for k in range(K):
            pts = []
            for it, vao, Is, b in per_item:
                F = Is[b[k]:b[k + 1]]
                if len(F):
                    pts.append(it["P"][F.reshape(-1)])
            if not pts:
                continue
            pts = np.vstack(pts)
            cen = pts.mean(0)
            outward = cen - cc
            outward[1] *= 0.6
            ol = np.linalg.norm(outward)
            outward = outward / ol if ol > 1e-6 else np.array([0.0, 1.0, 0.0])
            fr = frame_for(pts, outward)
            # a second card crossing the first gives the cluster volume
            fr2 = frame_with_normal(pts, fr[1] * 0.8 + fr[3] * 0.2)
            for f_ in (fr, fr2):
                rect, uvr = pk.next()
                baker.draw(rect, f_, [(vao, it["tex"], int(b[k]), int(b[k + 1] - b[k]), tint) for it, vao, Is, b in per_item])
                qp, qn, quv, qi = quad(f_, uvr)
                # card normals bent outward from the canopy centre: soft, volumetric shading
                bent = (qp - cc) * [1, 0.7, 1]
                bent /= np.maximum(np.linalg.norm(bent, axis=1, keepdims=True), 1e-6)
                qn = qn * 0.4 + bent * 0.6
                qn /= np.maximum(np.linalg.norm(qn, axis=1, keepdims=True), 1e-6)
                sway = np.clip(qp[:, 1] / H, 0.0, 1.0) ** 1.5
                parts.append((qp, qn, quv, qi, sway))
        for it, vao, Is, b in per_item:
            vao.release()
        lods.append({"cards": _merge(parts)})
    rgba, nor = baker.read()
    save_atlas(rgba, nor, os.path.join(out_dir, name + "_cards"))
    # wood LODs
    wood_tris = sum(len(w[3]) for w in wood)
    targets = (min(wood_tris, 5000), min(wood_tris, 700), min(wood_tris, 300))
    texmap = {}
    for li, tgt in enumerate(targets):
        pieces = {}
        for P, N, UV, I, m in wood:
            Pt = tr(P)
            if "branch" in m["name"].lower():               # small branches hide behind the cards
                lab = components(I, len(P))
                nl = lab.max() + 1
                mx = np.full((nl, 3), -1e9); mn_ = np.full((nl, 3), 1e9)
                np.maximum.at(mx, lab, Pt); np.minimum.at(mn_, lab, Pt)
                ext = (mx - mn_).max(1)
                keep = ext[lab[I[:, 0]]] > (0.07 if li == 0 else 0.25 if li == 1 else 0.38) * H
                if keep.sum() < 4:
                    keep = ext[lab[I[:, 0]]] >= ext.max() * 0.999
                I = I[keep]
            vid = np.unique(I)
            remap = np.full(len(P), -1, np.int64); remap[vid] = np.arange(len(vid))
            share = max(8, int(tgt * len(I) / max(1, wood_tris)))
            p2, n2, u2, i2 = decimate_welded(Pt[vid], N[vid], UV[vid], remap[I], share)
            sway = np.clip(p2[:, 1] / H, 0.0, 1.0) ** 2.2 * 0.35
            key = m["name"]
            pieces.setdefault(key, []).append((p2, n2, u2, i2, sway))
            texmap[key] = m
        lods[li]["wood"] = {k: _merge(v) for k, v in pieces.items()}
    mats = {}
    for key, m in texmap.items():
        base = f"{name}_{len(mats)}"
        for slot in ("base", "normal", "mr"):
            if m.get(slot):
                ext = os.path.splitext(m[slot])[1]
                shutil.copy(m[slot], os.path.join(out_dir, f"{base}_{slot}{ext}"))
        mats[key] = {k: (f"{base}_{k}{os.path.splitext(m[k])[1]}" if m.get(k) else None) for k in ("base", "normal", "mr")}
    # far LOD: impostor billboards baked from LOD0 (cards + wood)
    parts0 = []
    c0 = lods[0]["cards"]
    parts0.append((c0["P"], c0["N"], c0["UV"], c0["I"], os.path.join(out_dir, name + "_cards_albedo.png"), CARD_TINT))
    for key, msh in lods[0]["wood"].items():
        if msh is not None:
            parts0.append((msh["P"], msh["N"], msh["UV"], msh["I"], texmap[key]["base"],
                           WOOD_TINT.get(name, (1.05, 1.0, 0.95))))
    imp = bake_impostors(name, [parts0], out_dir)[0]
    lods[2] = {"imp": imp}
    octa = bake_octahedral(name, [parts0], out_dir)
    meta = {"kind": "tree", "height": float(H), "oct": octa, "imp": {"albedo": name + "_imp_albedo.png", "normal": name + "_imp_normal.jpg"}, "radius": float(np.percentile(np.linalg.norm(fol_pts[:, [0, 2]], axis=1), 95)),
            "cards": {"albedo": name + "_cards_albedo.png", "normal": name + "_cards_normal.jpg"}, "wood": mats}
    return [lods], meta


def build_clumps(name, src_dir, out_dir, baker):
    gname, maxv, tile, kind = CLUMPS[name]
    g = GLTF(os.path.join(src_dir, gname, gname + ".gltf"))
    tex = None
    groups = {}
    for nm, mi, P, N, UV, I in g.primitives():
        m = g.material(mi)
        tex = m["base"]
        groups.setdefault(nm, []).append((P, N, UV, I))
    # biggest variants first
    vs = sorted(groups.items(), key=lambda kv: -sum(len(x[3]) for x in kv[1]))[:maxv]
    pk = Packer(baker.size, tile)
    variants = []
    for nm, prims in vs:
        P = np.vstack([p[0] for p in prims]); N = np.vstack([p[1] for p in prims]); UV = np.vstack([p[2] for p in prims])
        I, base = [], 0
        for p in prims:
            I.append(p[3] + base); base += len(p[0])
        I = np.vstack(I)
        lo = P.min(0)
        cx, cz = (P[:, 0].max() + P[:, 0].min()) / 2, (P[:, 2].max() + P[:, 2].min()) / 2
        P = P - [cx, lo[1], cz]
        H = max(1e-3, P[:, 1].max())
        AO = np.clip(0.55 + 0.45 * (P[:, 1] / H) ** 0.7, 0.55, 1.0)
        vao = baker.upload(P, N, UV, AO, I)
        parts = []
        R = max(np.abs(P[:, [0, 2]]).max(), 1e-3)
        for k in range(3):
            a = math.pi * k / 3.0
            n = np.array([math.sin(a), 0.0, math.cos(a)])
            u = np.cross([0.0, 1.0, 0.0], n); u /= np.linalg.norm(u)
            v = np.array([0.0, 1.0, 0.0])
            c = np.array([0.0, H / 2, 0.0])
            fr = (c, u, v, n, np.array([R * 1.02, H / 2 * 1.02, R * 2 + 0.1]))
            rect, uvr = pk.next()
            baker.draw(rect, fr, [(vao, tex, 0, len(I), (1.0, 1.0, 1.0))])
            qp, qn, quv, qi = quad(fr, uvr)
            # crossed cards: normals tilted up a little for softer grass lighting
            qn = qn * 0.35 + np.array([0.0, 1.0, 0.0]) * 0.65
            qn /= np.linalg.norm(qn, axis=1, keepdims=True)
            parts.append((qp, qn, quv, qi, np.clip(qp[:, 1] / H, 0, 1) ** 1.3))
        vao.release()
        variants.append({"cards": _merge(parts), "height": float(H), "radius": float(R)})
    rgba, nor = baker.read()
    save_atlas(rgba, nor, os.path.join(out_dir, name + "_cards"))
    meta = {"kind": kind, "cards": {"albedo": name + "_cards_albedo.png", "normal": name + "_cards_normal.jpg"},
            "heights": [v["height"] for v in variants]}
    return [[{"cards": v["cards"]}] * 3 for v in variants], meta


def build_rock(name, src_dir, out_dir):
    gname, targets = ROCKS[name]
    g = GLTF(os.path.join(src_dir, gname, gname + ".gltf"))
    m = None
    variants = []
    for nm, mi, P, N, UV, I in g.primitives():
        m = g.material(mi)
        lo = P.min(0)
        cx, cz = (P[:, 0].max() + P[:, 0].min()) / 2, (P[:, 2].max() + P[:, 2].min()) / 2
        P = P - [cx, lo[1], cz]
        lods = []
        for t in targets:
            p2, n2, u2, i2 = decimate_welded(P, N, UV, I, t)
            lods.append({"rock": _merge([(p2, n2, u2, i2, np.zeros(len(p2)))])})
        variants.append(lods)
    mats = {}
    for slot in ("base", "normal", "mr"):
        if m.get(slot):
            im = Image.open(m[slot]).convert("RGB")
            if im.size[0] > 1024:
                im = im.resize((1024, 1024), Image.LANCZOS)
            im.save(os.path.join(out_dir, f"{name}_{slot}.jpg"), quality=90)
            mats[slot] = f"{name}_{slot}.jpg"
    sizes = [float((v[0]["rock"]["P"].max(0) - v[0]["rock"]["P"].min(0)).max()) for v in variants]
    return variants, {"kind": "rock", "mat": mats, "sizes": sizes}


def load_obj(path):
    V, VT, VN, F = [], [], [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "v":
            V.append([float(x) for x in t[1:4]])
        elif t[0] == "vt":
            VT.append([float(x) for x in t[1:3]])
        elif t[0] == "vn":
            VN.append([float(x) for x in t[1:4]])
        elif t[0] == "f":
            idx = [tuple(int(a) - 1 if a else -1 for a in (c.split("/") + ["", ""])[:3]) for c in t[1:]]
            for k in range(1, len(idx) - 1):
                F.append((idx[0], idx[k], idx[k + 1]))
    V, VT, VN = np.array(V), np.array(VT), np.array(VN)
    corners = np.array(F).reshape(-1, 3)              # (tri*3, [v, vt, vn])
    P = V[corners[:, 0]]
    UV = VT[corners[:, 1]] if len(VT) else np.zeros((len(P), 2))
    UV = np.column_stack([UV[:, 0], 1.0 - UV[:, 1]])  # OBJ (v up) -> glTF top-down
    if len(VN) and (corners[:, 2] >= 0).all():
        N = VN[corners[:, 2]]
    else:
        tri = P.reshape(-1, 3, 3)
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        N = np.repeat(fn, 3, axis=0)
    N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    I = np.arange(len(P)).reshape(-1, 3)
    return P, N, UV, I


def build_palm(name, palm_dir, out_dir):
    sub, objs, dif, nrm, spc, hr = PALMS[name]
    d = os.path.join(palm_dir, sub)
    variants = []
    for o in objs:
        P, N, UV, I = load_obj(os.path.join(d, o + ".obj"))
        y0 = P[:, 1].min()
        h = P[:, 1].max() - y0
        s = (hr[0] + hr[1]) / 2 / h
        # base of the trunk(s) at the origin
        low = P[P[:, 1] < y0 + 0.03 * h]
        cx, cz = low[:, 0].mean(), low[:, 2].mean()
        P = (P - [cx, y0, cz]) * s
        H = P[:, 1].max()
        sway = np.clip(P[:, 1] / H, 0, 1) ** 2.0
        mesh = _merge([(P, N, UV, I, sway)])
        variants.append([{"palm": mesh}, {"palm": mesh}, None])
    # textures: diffuse (RGBA alpha) -> png, normal -> png, specular -> roughness-ish 'mr' (G = roughness)
    if True:
        Image.open(os.path.join(d, dif)).convert("RGBA").resize((1024, 1024), Image.LANCZOS).save(
            os.path.join(out_dir, name + "_albedo.png"), optimize=True)
    Image.open(os.path.join(d, nrm)).convert("RGB").resize((1024, 1024), Image.LANCZOS).save(
        os.path.join(out_dir, name + "_normal.jpg"), quality=90)
    sp = np.asarray(Image.open(os.path.join(d, spc)).convert("L"), np.float32) / 255.0
    rough = np.clip(1.0 - sp * 0.8, 0.35, 1.0)
    mr = np.dstack([np.full(sp.shape, 255), (rough * 255), np.zeros(sp.shape)]).astype(np.uint8)
    Image.fromarray(mr, "RGB").resize((512, 512)).save(os.path.join(out_dir, name + "_mr.png"), optimize=True)
    tex = os.path.join(out_dir, name + "_albedo.png")
    imps = bake_impostors(name, [[(v[0]["palm"]["P"].astype(np.float64), v[0]["palm"]["N"].astype(np.float64),
                                   v[0]["palm"]["UV"].astype(np.float64), v[0]["palm"]["I"].astype(np.int64), tex, PALM_TINT)]
                                 for v in variants], out_dir)
    for v, im in zip(variants, imps):
        v[2] = {"imp": im}
    octa = bake_octahedral(name, [[(v[0]["palm"]["P"].astype(np.float64), v[0]["palm"]["N"].astype(np.float64),
                                    v[0]["palm"]["UV"].astype(np.float64), v[0]["palm"]["I"].astype(np.int64), tex, PALM_TINT)]
                                  for v in variants], out_dir)
    return variants, {"kind": "palm", "oct": octa, "imp": {"albedo": name + "_imp_albedo.png", "normal": name + "_imp_normal.jpg"},
                      "albedo": name + "_albedo.png", "normal": name + "_normal.jpg", "mr": name + "_mr.png",
                      "heights": list(hr)}


def save_npz(path, variants, meta):
    arrays = {}
    for vi, lods in enumerate(variants):
        for li, lod in enumerate(lods):
            for part, sub in lod.items():
                items = sub.items() if isinstance(sub, dict) and "P" not in sub else [(part, sub)]
                for key, mesh in items:
                    if mesh is None:
                        continue
                    tag = f"{vi}.{li}.{part}" + ("" if key == part else f".{key}")
                    for k in ("P", "N", "UV", "I", "S"):
                        arrays[f"{tag}.{k}"] = mesh[k]
    meta = dict(meta, variants=len(variants))
    arrays["meta.json"] = np.frombuffer(json.dumps(meta).encode(), np.uint8)
    np.savez_compressed(path, **arrays)


def build_terrain(src_dir, out_dir):
    for t in TERRAIN:
        d = os.path.join(src_dir, "tex", t)
        for src, dst, size in (("Diffuse.jpg", "albedo", 2048), ("nor_gl.jpg", "normal", 2048), ("arm.jpg", "arm", 1024)):
            im = Image.open(os.path.join(d, src)).convert("RGB")
            if im.size[0] != size:
                im = im.resize((size, size), Image.LANCZOS)
            im.save(os.path.join(out_dir, f"{t}_{dst}.jpg"), quality=90)
        print("terrain", t, flush=True)


def main():
    src, palm, out = sys.argv[1:4]
    only = set(sys.argv[4:])
    veg = os.path.join(out, "veg")
    ter = os.path.join(out, "terrain")
    os.makedirs(veg, exist_ok=True)
    os.makedirs(ter, exist_ok=True)

    def want(n):
        return not only or n in only
    if want("terrain"):
        build_terrain(src, ter)
    for name in TREES:
        if want(name):
            baker = Baker(2048)
            v, meta = build_tree(name, src, veg, baker)
            save_npz(os.path.join(veg, name + ".npz"), v, meta)
            print("tree", name, meta["height"], flush=True)
    for name in CLUMPS:
        if want(name):
            baker = Baker(1024 if CLUMPS[name][2] <= 256 and CLUMPS[name][1] * 3 <= 16 else 2048)
            v, meta = build_clumps(name, src, veg, baker)
            save_npz(os.path.join(veg, name + ".npz"), v, meta)
            print("clump", name, len(v), flush=True)
    for name in ROCKS:
        if want(name):
            v, meta = build_rock(name, src, veg)
            save_npz(os.path.join(veg, name + ".npz"), v, meta)
            print("rock", name, len(v), flush=True)
    for name in PALMS:
        if want(name):
            v, meta = build_palm(name, palm, veg)
            save_npz(os.path.join(veg, name + ".npz"), v, meta)
            print("palm", name, len(v), flush=True)


if __name__ == "__main__":
    main()

"""Textures land the right way round on FBX and .X models (needs a GPU / display):

    python tests/test_import_texture_mapping.py [shots folder]

Each case is a square facing the camera, textured with a 4-colour image
(red top-left, green top-right, blue bottom-left, yellow bottom-right).
The engine renders it and every corner of the square must show its colour:
any flip, swap, wrong UV set or missing texture fails.

FBX: ASCII, Z-up/centimetres, IndexToDirect UVs, texture on the
     SECOND UV set, LayeredTexture, embedded (and orphan-embedded) image,
     DDS texture, 2x tiling (Scaling) + clamp wrap, truncated last polygon,
     per-polygon materials, height-map "bump" ignored, cut-out alpha
.X:  text, binary-compressed, DeclData UVs, DDS-only texture, a mesh placed
     by a { reference } inside a Frame, short material list, GameGuru
     _D/_N texture set (normal map green flipped for OpenGL)
"""
import importlib.util
import io
import os
import shutil
import struct
import sys
import tempfile
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import pygame  # noqa: E402

res = []
W, HH = 480, 360


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


QUAD = {"TL": (255, 0, 0), "TR": (0, 255, 0), "BL": (0, 0, 255), "BR": (255, 255, 0)}


def quad_image(size=64, alpha_hole=False):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    h = size // 2
    s.fill(QUAD["TL"] + (255,), (0, 0, h, h))
    s.fill(QUAD["TR"] + (255,), (h, 0, h, h))
    s.fill(QUAD["BL"] + (255,), (0, h, h, h))
    s.fill(QUAD["BR"] + (255,), (h, h, h, h))
    if alpha_hole:
        s.fill((0, 0, 0, 0), (h - 4, h - 4, 8, 8))
    return s


def png_bytes(surf):
    buf = io.BytesIO()
    pygame.image.save(surf, buf, "x.png")
    return buf.getvalue()


def classify(px):
    r, g, b = [float(x) for x in px[:3]]
    m = max(r, g, b, 1.0)
    r, g, b = r / m, g / m, b / m
    if r > 0.6 and g > 0.6 and b < 0.45:
        return "BR"
    if r > 0.6 and g < 0.45 and b < 0.45:
        return "TL"
    if g > 0.6 and r < 0.45 and b < 0.45:
        return "TR"
    if b > 0.6 and r < 0.45 and g < 0.45:
        return "BL"
    return "?(%d,%d,%d)" % tuple(int(x) for x in px[:3])


# ------------------------------------------------------------------------- FBX writers
def fbx_ascii(objects, connections, up=1, unit=100.0):
    gs = ('GlobalSettings:  {\n Properties70:  {\n'
          '  P: "UpAxis", "int", "Integer", "",%d\n  P: "UpAxisSign", "int", "Integer", "",1\n'
          '  P: "FrontAxis", "int", "Integer", "",%d\n  P: "FrontAxisSign", "int", "Integer", "",%d\n'
          '  P: "CoordAxis", "int", "Integer", "",0\n  P: "CoordAxisSign", "int", "Integer", "",1\n'
          '  P: "UnitScaleFactor", "double", "Number", "",%s\n }\n}\n') % (
        up, 2 if up == 1 else 1, 1 if up == 1 else -1, unit)
    return ("; FBX 7.4.0 project file\nFBXHeaderExtension:  { FBXVersion: 7400 }\n" + gs +
            "Objects:  {\n" + objects + "}\nConnections:  {\n" + connections + "}\n")


def arr(vals):
    return "*%d { a: %s }" % (len(vals), ",".join(repr(float(v)) if isinstance(v, float) else str(v) for v in vals))


QV = [-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]              # Y-up square facing +Z
QI = [0, 1, 2, -4]
QUV = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]             # FBX: v up, (0,0) = image bottom-left


def fbx_quad(uv_layers=(("map1", QUV),), tex_uvset=None, layered=False, video=None, tex_file="quad.png",
             tex_props="", extra_objects="", extra_conns="", indices=QI, verts=QV, mat_slot="DiffuseColor",
             up=1, unit=100.0, mat_layer=None, materials=1):
    uvs = ""
    for k, (name, vals) in enumerate(uv_layers):
        uvs += ('  LayerElementUV: %d {\n   Name: "%s"\n   MappingInformationType: "ByPolygonVertex"\n'
                '   ReferenceInformationType: "IndexToDirect"\n   UV: %s\n   UVIndex: %s\n  }\n') % (
            k, name, arr(vals), arr(list(range(len(vals) // 2))))
    ml = ""
    if mat_layer is not None:
        ml = ('  LayerElementMaterial: 0 {\n   MappingInformationType: "ByPolygon"\n'
              '   ReferenceInformationType: "IndexToDirect"\n   Materials: %s\n  }\n') % arr(mat_layer)
    tp = tex_props
    if tex_uvset:
        tp += '   P: "UVSet", "KString", "", "", "%s"\n' % tex_uvset
    objs = ('Geometry: 10, "Geometry::q", "Mesh" {\n  Vertices: %s\n  PolygonVertexIndex: %s\n%s%s }\n'
            'Model: 20, "Model::q", "Mesh" {\n }\n') % (arr(verts), arr(indices), uvs, ml)
    conns = 'C: "OO",10,20\nC: "OO",20,0\n'
    for m in range(materials):
        col = "0.8,0.8,0.8" if m == 0 else "0.1,0.9,0.1"
        objs += 'Material: %d, "Material::m%d", "" {\n Properties70:  {\n  P: "DiffuseColor", "Color", "", "A",%s\n }\n}\n' % (
            30 + m, m, col)
        conns += 'C: "OO",%d,20\n' % (30 + m)
    objs += ('Texture: 40, "Texture::t", "" {\n Properties70:  {\n%s }\n FileName: "%s"\n RelativeFilename: "%s"\n}\n'
             % (tp, tex_file, tex_file))
    if layered:
        objs += 'LayeredTexture: 50, "LayeredTexture::lt", "" {\n}\n'
        conns += 'C: "OO",40,50\nC: "OP",50,30, "%s"\n' % mat_slot
    else:
        conns += 'C: "OP",40,30, "%s"\n' % mat_slot
    if video is not None:
        import base64
        objs += ('Video: 60, "Video::v", "Clip" {\n RelativeFilename: "%s"\n Content: , "%s"\n}\n'
                 % (video[0], base64.b64encode(video[1]).decode()))
        if video[2]:
            conns += 'C: "OO",60,40\n'
    return fbx_ascii(objs + extra_objects, conns + extra_conns, up=up, unit=unit)


# ------------------------------------------------------------------------- .X writers
X_HEAD = "xof 0303txt 0032\n"
XV = "4; -1;1;0;, 1;1;0;, 1;-1;0;, -1;-1;0;;\n"          # clockwise seen from -Z (Direct3D front)
XF = "1; 4;0,1,2,3;;\n"
XUV = "MeshTextureCoords { 4; 0;0;, 1;0;, 1;1;, 0;1;; }\n"   # DirectX: (0,0) = image top-left


def x_mat(tex="quad.png", name=None):
    nm = (" " + name) if name else ""
    return ("Material%s { 1;1;1;1;; 20.0; 0;0;0;; 0;0;0;; TextureFilename { \"%s\"; } }\n" % (nm, tex))


def x_quad(uv=XUV, mats=None, extra=""):
    mats = mats if mats is not None else ("MeshMaterialList { 1; 1; 0;; %s }\n" % x_mat())
    return X_HEAD + "Mesh quad {\n" + XV + XF + uv + mats + extra + "}\n"


def decl_uv_block():
    # one TEXCOORD0 float2 element, 4 vertices -> 8 DWORDs of float bits
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    dw = uv.view(np.uint32).ravel().tolist()
    return "DeclData { 1; 1;0;5;0;; %d; %s;; }\n" % (len(dw), ",".join(str(d) for d in dw))


def mszip(text):
    body = text[16:].encode()
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    block = b"CK" + co.compress(body) + co.flush()
    return b"xof 0303tzip0032" + struct.pack("<I", 16 + len(body)) + struct.pack("<HH", len(body), len(block)) + block


# ------------------------------------------------------------------------- render
def setup():
    work = tempfile.mkdtemp(prefix="h3dtexmap_")
    os.chdir(work)
    e = H.HeavenEngine(mode="runtime", width=W, height=HH)
    for n in e.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
        if n.is_clouds:
            n.visible = False
    return work, e


def render_corners(e, node, tag, flip_view=False):
    for n in list(e.scene.root.children):
        if n is not node and not (n.is_sun or n.is_clouds or n.is_ocean):
            n.remove()
    sz = 1.0
    try:
        lo, hi = e._spawn_mesh_local_aabb(node)
        sz = max(float(hi.x - lo.x), float(hi.y - lo.y)) / 2.0
        c = (lo + hi) * 0.5
    except Exception:
        c = glm.vec3(0)
    node.position = glm.vec3(0, 3, 0) - c
    e.camera.position = glm.vec3(0, 3, 3.2 * sz)
    e.camera.yaw, e.camera.pitch = 0.0, 0.0
    e.camera.near, e.camera.far = 0.05, 500.0
    for _ in range(3):
        e.renderer.render(e.scene, e.camera)
    e.ctx.finish()
    raw = e.ctx.screen.read(components=3)
    img = np.frombuffer(raw, np.uint8).reshape(HH, W, 3)[::-1]
    if SHOTS:
        pygame.image.save(pygame.image.frombuffer(np.ascontiguousarray(img).tobytes(), (W, HH), "RGB"),
                          os.path.join(SHOTS, tag.replace(" ", "_").replace("/", "_") + ".png"))
    d = int(HH * 0.08)
    cx, cy = W // 2, HH // 2
    got = {}
    for name, (dx, dy) in {"TL": (-1, -1), "TR": (1, -1), "BL": (-1, 1), "BR": (1, 1)}.items():
        y, x = cy + dy * d, cx + dx * d
        got[name] = classify(img[y - 2:y + 3, x - 2:x + 3].reshape(-1, 3).mean(axis=0))
    return got


def expect_quad(e, node, tag):
    got = render_corners(e, node, tag)
    ok = all(got[k] == k for k in QUAD)
    check(tag + ": texture the right way round", ok, got)
    return ok


def main():
    work, e = setup()
    img = quad_image()
    pygame.image.save(img, os.path.join(work, "quad.png"))
    pygame.image.save(img, os.path.join(work, "QUAD_upper.png"))
    from PIL import Image as PILImage
    PILImage.frombytes("RGBA", img.get_size(), pygame.image.tostring(img, "RGBA")).convert("RGB").save(
        os.path.join(work, "only.dds"), pixel_format="DXT1")

    def load(name, text, binary=False):
        p = os.path.join(work, name)
        with open(p, "wb" if binary else "w") as f:
            f.write(text)
        return e.load_model(p)

    # ---- FBX ----
    expect_quad(e, load("a.fbx", fbx_quad()), "fbx ascii")
    zup = [-100, 0, -100, 100, 0, -100, 100, 0, 100, -100, 0, 100]    # Z-up cm: x right, z up
    expect_quad(e, load("zup.fbx", fbx_quad(verts=zup, up=2, unit=1.0)), "fbx Z-up centimetres")
    junk = [0.5] * 8
    expect_quad(e, load("uv2.fbx", fbx_quad(uv_layers=(("UVMap", junk), ("Lightmap", QUV)), tex_uvset="Lightmap")),
                "fbx texture on the 2nd UV set")
    expect_quad(e, load("layered.fbx", fbx_quad(layered=True)), "fbx LayeredTexture")
    expect_quad(e, load("emb.fbx", fbx_quad(tex_file="missing_on_disk.png",
                                             video=("missing_on_disk.png", png_bytes(img), True))),
                "fbx embedded texture")
    expect_quad(e, load("orphan.fbx", fbx_quad(tex_file="gone.png", video=("gone.png", png_bytes(img), False))),
                "fbx orphan embedded texture")
    expect_quad(e, load("case.fbx", fbx_quad(tex_file="C:\\Users\\artist\\maps\\quad_UPPER.PNG")),
                "fbx Windows path, different letter case")
    expect_quad(e, load("dds.fbx", fbx_quad(tex_file="only.dds")), "fbx DDS texture")
    # a path from the artist's machine; the texture sits in a sibling folder here
    os.makedirs(os.path.join(work, "models", "FBX"))
    os.makedirs(os.path.join(work, "models", "OBJ"))
    pygame.image.save(img, os.path.join(work, "models", "OBJ", "SpiderTex.JPG.png"))
    p = os.path.join(work, "models", "FBX", "far.fbx")
    open(p, "w").write(fbx_quad(tex_file="D:\\projects\\game\\models\\obj\\spidertex.jpg.PNG"))
    expect_quad(e, e.load_model(p), "fbx other-machine path -> sibling folder found")
    os.chdir(os.path.join(work, "models", "OBJ"))      # a bare name must not be read from the cwd
    p2 = os.path.join(work, "models", "FBX", "bare.fbx")
    open(p2, "w").write(fbx_quad(tex_file="SpiderTex.JPG.png"))
    parts_, _l = H.load_fbx_parts(p2)
    check("fbx a bare texture name is not looked up in the working folder",
          not parts_[0]["material_info"]["_h3d"]["slots"])
    os.chdir(work)
    expect_quad(e, load("trunc.fbx", fbx_quad(indices=[0, 1, 2, 3])), "fbx truncated last polygon")
    # tiling: Scaling 2 + clamp -> the image in the lower-left quarter (FBX v up), edges stretched
    tiled = load("tile.fbx", fbx_quad(tex_props='   P: "Scaling", "Vector", "", "A",2,2,1\n'
                                                '   P: "WrapModeU", "enum", "", "",1\n'
                                                '   P: "WrapModeV", "enum", "", "",1\n'))
    parts = [tiled] + [c for c in tiled.children if getattr(c, "_heaven3d_imported_part", False)]
    ext = getattr(parts[0].material, "_h3d", None)
    check("fbx Scaling/WrapMode reach the material", ext and ext["xf_on"][0] == 1 and ext["wrap"][:2] == [1, 1],
          ext and (ext["xf_on"][0], ext["wrap"][:2]))
    got = render_corners(e, tiled, "fbx tiling 2x clamped")
    # uv*2 puts the whole image in the bottom-left quarter; the sample points (quad uv
    # 0.37 / 0.63) read texture uv 0.74 / 1.26 -> clamped -> all in the top-right (green) part
    check("fbx 2x tiling + clamp", all(v == "TR" for v in got.values()), got)
    # numeric check of the transform: engine uv -> FBX uv -> *2 -> engine uv
    xf = np.array(ext["xf"][:9], np.float32).reshape(3, 3).T
    out = xf @ np.array([0.25, 0.25, 1.0])
    check("fbx UV transform maths", bool(np.allclose(out[:2], [0.5, 0.5])), out[:2].tolist())

    two = load("twomat.fbx", fbx_quad(verts=[-1, -1, 0, 0, -1, 0, 0, 1, 0, -1, 1, 0,
                                             0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0],
                                      indices=[0, 1, 2, -4, 4, 5, 6, -8],
                                      uv_layers=(("map1", QUV + QUV),), mat_layer=[0, 1], materials=2))
    ps = [two] + [c for c in two.children if getattr(c, "_heaven3d_imported_part", False)]
    cols = sorted(tuple(round(float(x), 2) for x in p.material.base_color[:3]) for p in ps)
    check("fbx per-polygon materials make one part each", len(ps) == 2 and cols[0] != cols[1], cols)

    grey = pygame.Surface((32, 32))
    grey.fill((128, 128, 128))
    pygame.image.save(grey, os.path.join(work, "height.png"))
    blue = pygame.Surface((32, 32))
    blue.fill((128, 128, 255))
    pygame.image.save(blue, os.path.join(work, "nrm.png"))
    bump_ok = True
    for f, want in (("height.png", False), ("nrm.png", True)):
        txt = fbx_quad(extra_objects='Texture: 41, "Texture::b", "" {\n FileName: "%s"\n}\n' % f,
                       extra_conns='C: "OP",41,30, "Bump"\n')
        p = os.path.join(work, "bump_%s.fbx" % f)
        open(p, "w").write(txt)
        parts_, _l = H.load_fbx_parts(p)
        has = "normal" in parts_[0]["material_info"]["_h3d"]["slots"]
        bump_ok = bump_ok and has == want
    check("fbx Bump: grey height maps ignored, real normal maps used", bump_ok)
    cut = os.path.join(work, "cut.png")
    pygame.image.save(quad_image(alpha_hole=True), cut)
    txt = fbx_quad(tex_file="cut.png", extra_objects='Texture: 42, "Texture::o", "" {\n FileName: "cut.png"\n}\n',
                   extra_conns='C: "OP",42,30, "TransparentColor"\n')
    p = os.path.join(work, "cutout.fbx")
    open(p, "w").write(txt)
    mi = H.load_fbx_parts(p)[0][0]["material_info"]
    check("fbx opacity map = colour texture -> cut-out (MASK)", mi["alpha_mode"] == "MASK", mi["alpha_mode"])

    # ---- .X ----
    expect_quad(e, load("q.x", x_quad()), ".x text")
    expect_quad(e, load("qz.x", mszip(x_quad()), binary=True), ".x compressed (tzip)")
    expect_quad(e, load("decl.x", x_quad(uv=decl_uv_block())), ".x DeclData UVs")
    expect_quad(e, load("dds.x", x_quad(mats="MeshMaterialList { 1; 1; 0;; %s }\n" % x_mat("only.dds"))),
                ".x DDS-only texture")
    expect_quad(e, load("png_for_dds.x", x_quad(mats="MeshMaterialList { 1; 1; 0;; %s }\n" % x_mat("quad.dds"))),
                ".x .dds name, .png on disk")
    ref = (X_HEAD + x_mat(name="QuadMat") +
           "Mesh quadmesh {\n" + XV + XF + XUV + "MeshMaterialList { 1; 1; 0;; { QuadMat } }\n}\n" +
           "Frame Root {\n FrameTransformMatrix { 1,0,0,0, 0,1,0,0, 0,0,1,0, 5,0,0,1;; }\n { quadmesh }\n}\n")
    p = os.path.join(work, "ref.x")
    open(p, "w").write(ref)
    parts_, _l = H.load_x_parts(p)
    check(".x mesh placed by a {reference} in a Frame: once, moved by the frame",
          len(parts_) == 1 and np.allclose(parts_[0]["positions"][:, 0].mean(), 5.0), len(parts_))
    expect_quad(e, e.load_model(p), ".x referenced mesh + named material")
    short = (X_HEAD + "Mesh two {\n 8; -1;1;0;, 0;1;0;, 0;-1;0;, -1;-1;0;, 0;1;0;, 1;1;0;, 1;-1;0;, 0;-1;0;;\n"
             " 2; 4;0,1,2,3;, 4;4,5,6,7;;\n MeshTextureCoords { 8; 0;0;, 0.5;0;, 0.5;1;, 0;1;, 0.5;0;, 1;0;, 1;1;, 0.5;1;; }\n"
             " MeshMaterialList { 1; 1; 0;; %s }\n}\n" % x_mat())
    p = os.path.join(work, "short.x")
    open(p, "w").write(short)
    try:
        parts_, _l = H.load_x_parts(p)
        check(".x material list shorter than the faces: no crash, every face kept",
              sum(len(q["indices"]) for q in parts_) == 12)
    except Exception as ex:   # noqa: BLE001
        check(".x material list shorter than the faces: no crash", False, repr(ex))
    expect_quad(e, e.load_model(p), ".x two faces, one material entry")
    # GameGuru set
    pygame.image.save(img, os.path.join(work, "crate_D.png"))
    p = os.path.join(work, "gg.x")
    open(p, "w").write(x_quad(mats="MeshMaterialList { 1; 1; 0;; %s }\n" % x_mat("crate_D.dds")))
    expect_quad(e, e.load_model(p), ".x GameGuru _D texture (.dds name, .png file)")
    nm = pygame.Surface((16, 16))
    nm.fill((128, 60, 255))                         # DirectX: green < 128 = "up" in OpenGL terms
    pygame.image.save(nm, os.path.join(work, "crate_N.png"))
    p = os.path.join(work, "gg.x")
    open(p, "w").write(x_quad(mats="MeshMaterialList { 1; 1; 0;; %s }\n" % x_mat("crate_D.dds")))
    mi = H.load_x_parts(p)[0][0]["material_info"]
    nslot = mi["_h3d"]["slots"].get("normal")
    g = None
    if nslot:
        s = pygame.image.load(io.BytesIO(nslot["desc"][1]))
        g = s.get_at((3, 3))[1]
    check(".x GameGuru _D/_N: normal map found, green flipped for OpenGL", nslot is not None and g == 195, g)

    # ---- the real-world corpus still maps (when present) ----
    shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)

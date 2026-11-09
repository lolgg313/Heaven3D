"""FBX and DirectX .x importers (no GPU needed):

    python tests/test_fbx_x_import.py [assimp/test folder]

Writes small files by hand and checks what the importers make of them:

  * ASCII FBX: Z-up centimetres -> Y-up metres, the model's transform, UVs
    with V flipped, quads fanned into triangles, a material colour
  * binary FBX 7.4 with zlib-packed arrays: the same cube comes back
  * binary FBX 7.5 (64-bit record headers)
  * text .x: left-handed -> right-handed, FrameTransformMatrix, a texture
    found on disk regardless of case, emissive textured like Direct3D does
  * compressed binary .x (MSZIP) gives the same mesh as the text one
  * a damaged file raises a readable error instead of returning nothing

When an assimp test folder is given (or found at ../ext/assimp/test), every
.fbx / .x in it is parsed too and must give finite geometry.
"""
import importlib.util
import os
import struct
import sys
import tempfile
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ARGS = sys.argv[1:]
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    d = "" if isinstance(d, str) and not d else str(d)
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


# a unit quad (two triangles once fanned) 200 cm wide in X/Y, Z-up file
QUAD_V = [-100, -100, 0, 100, -100, 0, 100, 100, 0, -100, 100, 0]
QUAD_I = [0, 1, 2, -4]                       # last index is ~3
QUAD_UV = [0, 0, 1, 0, 1, 1, 0, 1]


def ascii_fbx():
    return """; FBX 7.4.0 project file
FBXHeaderExtension:  { FBXVersion: 7400 }
GlobalSettings:  {
    Properties70:  {
        P: "UpAxis", "int", "Integer", "",2
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",1
        P: "FrontAxisSign", "int", "Integer", "",-1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",1
    }
}
Objects:  {
    Geometry: 10, "Geometry::quad", "Mesh" {
        Vertices: *12 { a: %s }
        PolygonVertexIndex: *4 { a: %s }
        LayerElementUV: 0 {
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "IndexToDirect"
            UV: *8 { a: %s }
            UVIndex: *4 { a: 0,1,2,3 }
        }
    }
    Model: 20, "Model::quad", "Mesh" {
        Properties70:  {
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,300
        }
    }
    Material: 30, "Material::red", "" {
        Properties70:  {
            P: "DiffuseColor", "Color", "", "A",1,0,0
        }
    }
}
Connections:  {
    C: "OO",10,20
    C: "OO",30,20
    C: "OO",20,0
}
""" % (",".join(map(str, QUAD_V)), ",".join(map(str, QUAD_I)), ",".join(map(str, QUAD_UV)))


# ---- a tiny binary FBX writer -------------------------------------------
def _prop(v):
    if isinstance(v, str):
        b = v.encode()
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, bool):
        return b"C" + struct.pack("<?", v)
    if isinstance(v, int):
        return b"L" + struct.pack("<q", v)
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, tuple) and v[0] in "di":   # (kind, values) -> zlib array
        fmt = "<%d%s" % (len(v[1]), "d" if v[0] == "d" else "i")
        raw = zlib.compress(struct.pack(fmt, *v[1]))
        return v[0].encode() + struct.pack("<III", len(v[1]), 1, len(raw)) + raw
    raise TypeError(v)


def _node(name, props=(), children=(), off=0, wide=False):
    """Serialise one record whose first byte sits at file offset `off`."""
    pb = b"".join(_prop(p) for p in props)
    hdr = 25 if wide else 13
    nb = name.encode()
    body_off = off + hdr + len(nb) + len(pb)
    kids = b""
    for c in children:
        kids += c(body_off + len(kids))
    if children:
        kids += b"\0" * hdr
    end = off + hdr + len(nb) + len(pb) + len(kids)
    if wide:
        head = struct.pack("<QQQB", end, len(props), len(pb), len(nb))
    else:
        head = struct.pack("<IIIB", end, len(props), len(pb), len(nb))
    return head + nb + pb + kids


def N(name, props=(), children=(), wide=False):
    return lambda off: _node(name, props, children, off, wide)


def binary_fbx(version=7400):
    wide = version >= 7500
    P = lambda *a: N("P", a, wide=wide)  # noqa: E731
    tree = [
        N("GlobalSettings", (), [N("Properties70", (), [
            P("UpAxis", "int", "Integer", "", 2), P("UpAxisSign", "int", "Integer", "", 1),
            P("FrontAxis", "int", "Integer", "", 1), P("FrontAxisSign", "int", "Integer", "", -1),
            P("CoordAxis", "int", "Integer", "", 0), P("CoordAxisSign", "int", "Integer", "", 1),
            P("UnitScaleFactor", "double", "Number", "", 1.0)], wide)], wide),
        N("Objects", (), [
            N("Geometry", (10, "quad\x00\x01Geometry", "Mesh"), [
                N("Vertices", (("d", [float(x) for x in QUAD_V]),), wide=wide),
                N("PolygonVertexIndex", (("i", QUAD_I),), wide=wide),
                N("LayerElementUV", (0,), [
                    N("MappingInformationType", ("ByPolygonVertex",), wide=wide),
                    N("ReferenceInformationType", ("Direct",), wide=wide),
                    N("UV", (("d", [float(x) for x in QUAD_UV]),), wide=wide)], wide)], wide),
            N("Model", (20, "quad\x00\x01Model", "Mesh"), [N("Properties70", (), [
                P("Lcl Translation", "Lcl Translation", "", "A", 0.0, 0.0, 300.0)], wide)], wide),
            N("Material", (30, "red\x00\x01Material", ""), [N("Properties70", (), [
                P("DiffuseColor", "Color", "", "A", 1.0, 0.0, 0.0)], wide)], wide)], wide),
        N("Connections", (), [N("C", ("OO", 10, 20), wide=wide), N("C", ("OO", 30, 20), wide=wide),
                              N("C", ("OO", 20, 0), wide=wide)], wide),
    ]
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    for t in tree:
        out += t(len(out))
    return out + b"\0" * (25 if wide else 13) + b"\0" * 160


def check_quad(tag, parts):
    check(tag + ": one part", len(parts) == 1, len(parts))
    if not parts:
        return
    p = parts[0]
    pos, idx = np.asarray(p["positions"]), np.asarray(p["indices"])
    check(tag + ": quad fanned into two triangles", len(idx) == 6, len(idx))
    lo, hi = pos.min(0), pos.max(0)
    # Z-up cm -> Y-up m: file X stays X, file Y (depth) -> -Z, file Z (up) -> Y
    check(tag + ": centimetres become metres", np.allclose(hi - lo, [2, 0, 2], atol=1e-4), (lo, hi))
    check(tag + ": Z-up translation becomes height", np.allclose(pos[:, 1], 3.0, atol=1e-4), pos[:, 1])
    uv = np.asarray(p["uv0"])
    check(tag + ": UVs kept (FBX and the engine both start bottom-left)",
          np.isclose(uv[:, 1].min(), 0) and np.isclose(uv[:, 1].max(), 1) and
          np.allclose(sorted(set(np.round(uv[:, 1], 4))), [0, 1]), uv.tolist())
    n = np.asarray(p["normals"])
    check(tag + ": normals point up", np.allclose(n, [0, 1, 0], atol=1e-3), n[:2].tolist())
    check(tag + ": material colour read", np.allclose(p["material_info"]["base_color"][:3], [1, 0, 0]),
          p["material_info"]["base_color"])


X_TEXT = """xof 0303txt 0032
Frame Root {
  FrameTransformMatrix { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,2,0,1;; }
  Mesh tri {
    3; 0;0;0;, 1;0;0;, 0;1;0;;
    1; 3;0,1,2;;
    MeshTextureCoords { 3; 0;1;, 1;1;, 0;0;; }
    MeshMaterialList { 1; 1; 0;;
      Material { 1.0;1.0;1.0;1.0;; 20.0; 0.0;0.0;0.0;; 0.4;0.4;0.4;;
        TextureFilename { "Skin.PNG"; }
      }
    }
  }
}
"""


def mszip(txt_body):
    """Wrap a binary-tokens-free text .x body into the 'tzip' container."""
    raw = txt_body.encode()
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    block = b"CK" + co.compress(raw) + co.flush()
    return (b"xof 0303tzip0032" + struct.pack("<I", 16 + len(raw)) +
            struct.pack("<HH", len(raw), len(block)) + block)


def main():
    tmp = tempfile.mkdtemp(prefix="h3dfbx_")
    try:
        f = os.path.join(tmp, "quad_ascii.fbx")
        open(f, "w").write(ascii_fbx())
        check_quad("ascii fbx", H.load_fbx_parts(f)[0])
    except Exception as e:  # noqa: BLE001
        check("ascii fbx loads", False, repr(e))
    for ver in (7400, 7500):
        try:
            f = os.path.join(tmp, "quad_%d.fbx" % ver)
            open(f, "wb").write(binary_fbx(ver))
            check_quad("binary fbx %d" % ver, H.load_fbx_parts(f)[0])
        except Exception as e:  # noqa: BLE001
            check("binary fbx %d loads" % ver, False, repr(e))
    bad = os.path.join(tmp, "broken.fbx")
    open(bad, "wb").write(b"Kaydara FBX Binary\x20\x20\x20\x1a\x00" + b"\x00" * 64)
    try:
        H.load_fbx_parts(bad)
        check("damaged fbx raises a readable error", False, "no error")
    except Exception as e:  # noqa: BLE001
        check("damaged fbx raises a readable error", "FBX" in str(e), str(e))

    # ---- .x ----
    open(os.path.join(tmp, "skin.png"), "wb").write(b"\x89PNG\r\n\x1a\n")
    xt = os.path.join(tmp, "tri.x")
    open(xt, "w").write(X_TEXT)
    try:
        parts = H.load_x_parts(xt)[0]
        check(".x text: one part", len(parts) == 1, len(parts))
        p = parts[0]
        pos = np.asarray(p["positions"])
        check(".x text: FrameTransformMatrix applied", np.allclose(pos[:, 1].min(), 2.0), pos.tolist())
        check(".x text: Z mirrored for right-handed space", np.allclose(pos[:, 2], 0.0), pos.tolist())
        # Direct3D fronts are clockwise, so this triangle faces +Z in the
        # left-handed file; mirrored into right-handed space it faces -Z,
        # and the counter-clockwise winding must agree with that
        a, b, c = pos[np.asarray(p["indices"])[:3]]
        nrm = np.cross(b - a, c - a)
        check(".x text: winding reversed with the mirror", nrm[2] < 0, nrm.tolist())
        slots = p["material_info"]["_h3d"]["slots"]
        bc = slots.get("base_color", {}).get("desc")
        check(".x text: texture found regardless of case",
              bc is not None and os.path.basename(bc[1]) == "skin.png", bc)
        check(".x text: emissive modulated by the texture (Direct3D)", "emissive" in slots, list(slots))
        # DirectX v=0 is the image top; the engine's import parts count v from the bottom
        check(".x text: UV v flipped to the engine convention",
              np.allclose(sorted(np.asarray(p["uv0"])[:, 1]), [0, 0, 1]), p["uv0"].tolist())
        xz = os.path.join(tmp, "tri_zip.x")
        open(xz, "wb").write(mszip(X_TEXT[16:]))
        pz = H.load_x_parts(xz)[0]
        check(".x tzip (MSZIP) gives the same mesh",
              len(pz) == 1 and np.allclose(pz[0]["positions"], p["positions"]), len(pz))
        check(".x sidecar textures listed for export",
              [r for r, _a in H.model_texture_files(xt)] == ["skin.png"], H.model_texture_files(xt))
    except Exception as e:  # noqa: BLE001
        check(".x loads", False, repr(e))

    # ---- optional corpus sweep ----
    corpus = ARGS[0] if ARGS else os.path.join(os.path.dirname(ROOT), "ext", "assimp", "test")
    if os.path.isdir(corpus):
        n_ok = n_all = 0
        bad_files = []
        for dp, _dn, fn in os.walk(corpus):
            for name in sorted(fn):
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".fbx", ".x"):
                    continue
                path = os.path.join(dp, name)
                n_all += 1
                try:
                    parts = (H.load_fbx_parts if ext == ".fbx" else H.load_x_parts)(path)[0]
                    fin = all(np.isfinite(q["positions"]).all() and len(q["indices"]) % 3 == 0 and
                              (len(q["indices"]) == 0 or int(np.max(q["indices"])) < len(q["positions"]))
                              for q in parts)
                    if parts and fin:
                        n_ok += 1
                    else:
                        bad_files.append(name)
                except Exception as e:  # noqa: BLE001
                    if "damaged" in str(e) or "too old" in str(e) or "ASCII FBX 6" in str(e):
                        n_ok += 1          # refused with a clear message
                    else:
                        bad_files.append("%s (%s)" % (name, e))
        check("corpus: %d/%d files import or are refused clearly" % (n_ok, n_all),
              n_ok == n_all, "; ".join(bad_files[:6]))
    else:
        print("SKIP corpus sweep (no assimp test folder)")
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
